import logging, re
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse
from dask_gateway.auth import JupyterHubAuth
from dask.utils import format_bytes
from dask_gateway import Gateway
from dask_gateway.client import ClusterReport, ClusterStatus, GatewayCluster
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jupyterhub.services.auth import HubOAuth
from starlette.middleware.sessions import SessionMiddleware



app_dir = Path(__file__).parent.resolve()

index_html = app_dir / "index.html"

LOG_MODE = os.environ.get("DASK_GATEWAY_DASHBOARD_LOG_MODE", "prod").strip().lower()

logger = logging.getLogger("uvicorn.error")
uvicorn_access_logger = logging.getLogger("uvicorn.access")
if LOG_MODE == "dev":
    uvicorn_access_logger.disabled = False
    uvicorn_access_logger.setLevel(logging.INFO)
    logger.setLevel(logging.INFO)
else:
    uvicorn_access_logger.disabled = True

app = FastAPI(root_path=os.environ.get("ROOT_PATH", ""))

app.add_middleware(
    SessionMiddleware, secret_key=os.environ.get("SESSION_ENCRYPT_TOKEN", secrets.token_hex(32)).strip()
)

static_dir = app_dir / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

prefix = "dask-gateway-lampata-test"

WORKSPACE_URL_HEADER = "X-Workspace-Url"

SERVICE_DOMAIN_TEMPLATE = os.environ.get("DASK_GATEWAY_SERVICE_DOMAIN_TEMPLATE", "{namespace}.svc.cluster.local")
ADDRESS_TEMPLATE = os.environ.get(
    "DASK_GATEWAY_ADDRESS_TEMPLATE", "http://api-dask-gateway.{service_domain}:8000"
)
PROXY_ADDRESS_TEMPLATE = os.environ.get(
    "DASK_GATEWAY_PROXY_ADDRESS_TEMPLATE", "tcp://traefik-dask-gateway.{service_domain}:8786"
)
PUBLIC_ADDRESS_TEMPLATE = os.environ.get("DASK_GATEWAY_PUBLIC_ADDRESS_TEMPLATE")

auth_is_jupyterhub = os.environ.get("DASK_GATEWAY__AUTH__TYPE", '') == "jupyterhub"
auth = None
if auth_is_jupyterhub:
    jhub_api_url = os.environ.get("JUPYTERHUB_API_URL")
    auth = HubOAuth(
        api_token=os.environ.get("JUPYTERHUB_API_TOKEN", "token").strip(),
        api_url=jhub_api_url,
        cache_max_age=60,
        oauth_client_id=os.environ.get("OATH_CLIENT_ID", None),
        oauth_redirect_uri=os.environ.get("REDIR_URL", None),
        access_scope=set()
    )


class _RedactOAuthAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "/oauth_callback" in msg:
            msg = re.sub(r'\?[^" ]*', '?REDACTED', msg)
            record.msg, record.args = msg, ()
        return True

logging.getLogger().addFilter(_RedactOAuthAccess())            # root logger
logging.getLogger("uvicorn.access").addFilter(_RedactOAuthAccess())


class ClusterModel(TypedDict):
    name: str
    status: str
    dashboard_link: str
    workers: int
    cores: float
    memory: str
    started: float


def make_cluster_model(cluster: GatewayCluster | ClusterReport) -> ClusterModel:
    """Make a single cluster model"""
    # derived from dask-labextension Manager
    if isinstance(cluster, ClusterReport):
        workers = cores = 0
        memory = "0 B"
        started = int(cluster.start_time.timestamp())
        status = cluster.status.name
    else:
        info = cluster.scheduler_info
        status = "RUNNING"
        started = info["started"]
        cores = sum(d["nthreads"] for d in info["workers"].values())
        workers = len(info["workers"])
        memory = format_bytes(sum(d["memory_limit"] for d in info["workers"].values()))
    return {
        "name": cluster.name,
        "status": status,
        "dashboard_link": cluster.dashboard_link,
        "workers": workers,
        "cores": cores,
        "memory": memory,
        "started": started,
    }


_NAMESPACE_HOST_PATTERN = re.compile(r"^workspace\.([a-z0-9-]+)\.hub-otc\.eox\.at$")


def _extract_namespace_from_url(workspace_url: str) -> str:
    parsed = urlparse(workspace_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Workspace URL must use http or https")
    host = parsed.hostname
    if not host:
        raise ValueError("Workspace URL is missing a hostname")
    if not host.endswith(".eox.at"):
        raise ValueError("Workspace URL must originate from eox.at")
    match = _NAMESPACE_HOST_PATTERN.fullmatch(host)
    if not match:
        raise ValueError("Workspace URL does not include a namespace in the expected format")
    namespace = match.group(1)
    if not namespace:
        raise ValueError("Namespace extracted from workspace URL is empty")
    return namespace


def _get_namespace_from_request(request: Request) -> str:
    workspace_url = request.headers.get(WORKSPACE_URL_HEADER)
    if not workspace_url:
        raise HTTPException(status_code=400, detail=f"Missing {WORKSPACE_URL_HEADER} header")
    logger.info("Received workspace header %s", workspace_url)
    try:
        namespace = _extract_namespace_from_url(workspace_url)
        logger.info("Extracted namespace %s from workspace URL", namespace)
        return namespace
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _namespace_gateway_kwargs(namespace: str) -> dict[str, str]:
    try:
        service_domain = SERVICE_DOMAIN_TEMPLATE.format(namespace=namespace)
    except KeyError as exc:  # pragma: no cover - configuration error
        missing_key = exc.args[0]
        raise RuntimeError(
            f"Missing placeholder '{{{missing_key}}}' in service domain template configuration"
        ) from exc
    context = {
        "namespace": namespace,
        "service_domain": service_domain,
    }
    logger.info("Preparing gateway kwargs for namespace %s", namespace)
    try:
        kwargs = {
            "address": ADDRESS_TEMPLATE.format(**context),
            "proxy_address": PROXY_ADDRESS_TEMPLATE.format(**context),
        }
        if PUBLIC_ADDRESS_TEMPLATE:
            kwargs["public_address"] = PUBLIC_ADDRESS_TEMPLATE.format(**context)
        logger.info(
            "Gateway configuration for namespace %s with context %s resolved to %s",
            namespace,
            context,
            kwargs,
        )
        return kwargs
    except KeyError as exc:  # pragma: no cover - configuration error
        missing_key = exc.args[0]
        raise RuntimeError(
            f"Missing placeholder '{{{missing_key}}}' in gateway address template configuration"
        ) from exc


def _compose_gateway_kwargs(token: str | None, gateway_kwargs: dict | None = None) -> dict:
    kwargs = dict(gateway_kwargs or {})
    kwargs.setdefault("asynchronous", True)
    if token and auth_is_jupyterhub and "auth" not in kwargs:
        kwargs["auth"] = JupyterHubAuth(api_token=token)
    logger.info("Final gateway kwargs sent to Gateway: %s", kwargs)
    return kwargs


async def list_clusters(token: str | None = None, *, gateway_kwargs: dict | None = None) -> list[ClusterModel]:
    """List of cluster models"""
    clusters: list[ClusterModel] = []
    kwargs = _compose_gateway_kwargs(token, gateway_kwargs)
    async with Gateway(**kwargs) as gateway:
        for cluster_info in await gateway.list_clusters():
            cluster_name = cluster_info.name
            if cluster_info.status == ClusterStatus.RUNNING:
                async with gateway.connect(cluster_name) as cluster:
                    cluster_model = make_cluster_model(cluster)
            else:
                cluster_model = make_cluster_model(cluster_info)
            clusters.append(cluster_model)
    return clusters




@dataclass
class _MockCluster:
    """Mock cluster for UI development"""

    name: str
    workers: int
    cores_per_worker: int = 2

    @property
    def dashboard_link(self) -> str:
        return f"http://localhost:8000/cluster/{self.name}"

    @property
    def scheduler_info(self) -> dict:
        return {
            "started": time.time() - 3600,
            "workers": {
                f"id-{n}": {
                    "nthreads": self.cores_per_worker,
                    "memory_limit": 2 << 30,
                }
                for n in range(self.workers)
            },
        }


async def _mock_list_clusters(token: str | None = None, *, gateway_kwargs: dict | None = None) -> list[ClusterModel]:
    """mock cluster list for UI development"""
    clusters = []
    for i in range(3):
        cluster = _MockCluster(workers=i, name=f"username.{secrets.token_hex(16)}")
        clusters.append(make_cluster_model(cluster))
    return clusters



if os.environ.get("_GATEWAY_MOCK_CLUSTERS"):
    list_clusters = _mock_list_clusters

async def get_current_user(request: Request) -> dict | Response:
    if not auth_is_jupyterhub:
        return {"name": "shared", "admin": False}
    token = request.session.get("token")
    user = auth.user_for_token(token) if token else None
    if user:
        return user
    state = auth.generate_state(next_url=request.url.path)
    response = RedirectResponse(auth.login_url + f"&state={state}")
    response.set_cookie(auth.state_cookie_name, state)
    return response



@app.get("/")
async def get(request: Request):
    """serve the HTML page"""
    user = await get_current_user(request)
    if isinstance(user, Response):
        return user
    with index_html.open() as f:
        return HTMLResponse(f.read())


@app.get("/clusters")
async def get_clusters(request: Request):
    """Return list of clusters as JSON"""
    user = await get_current_user(request)
    if isinstance(user, Response):
        return user
    token = request.session.get("token")
    user_name = user.get("name") if isinstance(user, dict) else str(user)
    logger.info("Handling /clusters for user %s", user_name)
    namespace = _get_namespace_from_request(request)
    gateway_kwargs = _namespace_gateway_kwargs(namespace)
    clusters = await list_clusters(token, gateway_kwargs=gateway_kwargs)
    return JSONResponse(clusters)


@app.delete("/clusters/{cluster_id}")
async def stop_cluster(cluster_id: str, request: Request):
    """Stop a cluster"""
    user = await get_current_user(request)
    if isinstance(user, Response):
        return user
    token = request.session.get("token")
    namespace = _get_namespace_from_request(request)
    gateway_kwargs = _compose_gateway_kwargs(token, _namespace_gateway_kwargs(namespace))
    logger.info("Stopping cluster %s in namespace %s", cluster_id, namespace)
    async with Gateway(**gateway_kwargs) as gateway:
        try:
            await gateway.get_cluster(cluster_id)
        except ValueError:
            return JSONResponse(
                status_code=404, content={"message": f"No such cluster: {cluster_id}"}
            )

        await gateway.stop_cluster(cluster_id)
    return JSONResponse({"status": "ok"})




@app.get("/oauth_callback")
async def oauth_callback(request: Request):
    code = request.query_params.get("code")
    if code is None:
        return Response("Forbidden", status_code=403)

    arg_state = request.query_params.get("state")
    cookie_state = request.cookies.get(auth.state_cookie_name)
    if arg_state is None or arg_state != cookie_state:
        return Response("Forbidden", status_code=403)

    token = auth.token_for_code(code)
    request.session["token"] = token
    next_url = auth.get_next_url(cookie_state) or prefix
    return RedirectResponse(next_url)

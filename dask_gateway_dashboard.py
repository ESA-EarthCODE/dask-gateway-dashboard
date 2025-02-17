import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from dask.utils import format_bytes
from dask_gateway import Gateway
from dask_gateway.client import ClusterReport, ClusterStatus, GatewayCluster
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app_dir = Path(__file__).parent.resolve()
index_html = app_dir / "index.html"
log = logging.getLogger("uvicorn.error")
app = FastAPI()

app.mount("/static", StaticFiles(directory=app_dir / "static"), name="static")


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


async def list_clusters() -> list[ClusterModel]:
    """List of cluster models"""
    clusters: list[ClusterModel] = []
    async with Gateway(asynchronous=True) as gateway:
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


async def _mock_list_clusters() -> list[ClusterModel]:
    """mock cluster list for UI development"""
    clusters = []
    for i in range(3):
        cluster = _MockCluster(workers=i, name=f"username.{secrets.token_hex(16)}")
        clusters.append(make_cluster_model(cluster))
    return clusters


if os.environ.get("_GATEWAY_MOCK_CLUSTERS"):
    list_clusters = _mock_list_clusters


@app.get("/")
async def get():
    """serve the HTML page"""
    with index_html.open() as f:
        return HTMLResponse(f.read())


@app.get("/clusters")
async def get_clusters():
    """Return list of clusters as JSON"""
    clusters = await list_clusters()
    return JSONResponse(clusters)


@app.delete("/clusters/{cluster_id}")
async def stop_cluster(cluster_id: str):
    """Stop a cluster"""
    async with Gateway(asynchronous=True) as gateway:
        try:
            await gateway.get_cluster(cluster_id)
        except ValueError:
            return JSONResponse(
                status_code=404, content={"message": f"No such cluster: {cluster_id}"}
            )

        await gateway.stop_cluster(cluster_id)
    return JSONResponse({"status": "ok"})

import pytest

from dask_gateway_dashboard import (
    ADDRESS_TEMPLATE,
    PROXY_ADDRESS_TEMPLATE,
    PUBLIC_ADDRESS_TEMPLATE,
    SERVICE_DOMAIN_TEMPLATE,
    WORKSPACE_URL_HEADER,
    _namespace_gateway_kwargs,
    list_clusters,
)

# asyncio_default_test_loop_scope = session in pytest-asyncio 0.26
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_list_clusters(gateway):
    clusters = await list_clusters()
    assert clusters == []
    cluster = await gateway.new_cluster()
    clusters = await list_clusters()
    assert len(clusters) == 1
    c = clusters[0]
    assert c["name"] == cluster.name
    assert c["workers"] == 0
    assert c["cores"] == 0


async def test_get_page(client):
    page = await client.get("/")
    assert page.status_code == 200

# TODO: fix these tests to work with the new auth and system
# async def test_get_clusters(client, gateway):
#     page = await client.get(
#         "/clusters",
#         headers={WORKSPACE_URL_HEADER: "https://workspace.test.hub-otc.eox.at/"},
#     )
#     assert page.status_code == 200
#     cluster_list = page.json()
#     assert cluster_list == []

# TODO: fix these tests to work with the new auth and system
# def test_namespace_gateway_kwargs_defaults():
#     namespace = "demo"
#     service_domain = SERVICE_DOMAIN_TEMPLATE.format(namespace=namespace)
#     expected = {
#         "address": ADDRESS_TEMPLATE.format(namespace=namespace, service_domain=service_domain),
#         "proxy_address": PROXY_ADDRESS_TEMPLATE.format(
#             namespace=namespace, service_domain=service_domain
#         ),
#     }
#     if PUBLIC_ADDRESS_TEMPLATE:
#         expected["public_address"] = PUBLIC_ADDRESS_TEMPLATE.format(
#             namespace=namespace, service_domain=service_domain
#         )
#     assert _namespace_gateway_kwargs(namespace) == expected


# def test_namespace_gateway_kwargs_overrides(monkeypatch):
#     monkeypatch.setattr(
#         "dask_gateway_dashboard.SERVICE_DOMAIN_TEMPLATE",
#         "{namespace}.svc.local",
#         raising=False,
#     )
#     monkeypatch.setattr(
#         "dask_gateway_dashboard.ADDRESS_TEMPLATE",
#         "http://test-{namespace}",
#         raising=False,
#     )
#     monkeypatch.setattr(
#         "dask_gateway_dashboard.PROXY_ADDRESS_TEMPLATE",
#         "tcp://proxy-{service_domain}",
#         raising=False,
#     )
#     monkeypatch.setattr(
#         "dask_gateway_dashboard.PUBLIC_ADDRESS_TEMPLATE",
#         "https://public/{namespace}",
#         raising=False,
#     )
#     namespace = "team-a"
#     expected = {
#         "address": "http://test-team-a",
#         "proxy_address": "tcp://proxy-team-a.svc.local",
#         "public_address": "https://public/team-a",
#     }
#     assert _namespace_gateway_kwargs(namespace) == expected

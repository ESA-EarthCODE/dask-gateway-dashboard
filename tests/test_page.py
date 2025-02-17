import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from playwright.async_api import Page, expect
from uvicorn import Config, Server

from dask_gateway_dashboard import app

# asyncio_default_test_loop_scope = session in pytest-asyncio 0.26
pytestmark = pytest.mark.asyncio(loop_scope="session")

host: str = "127.0.0.1"
port: int = 9999
base_url: str = f"http://{host}:{port}"


@pytest.fixture(autouse=True)
async def server(gateway):
    config = Config(
        host=host,
        port=port,
        app=app,
        log_level="debug",
    )
    server = Server(config=config)
    cancel_handle = asyncio.ensure_future(server.serve())
    await asyncio.sleep(0.1)
    try:
        yield server
    finally:
        await server.shutdown()
        cancel_handle.cancel()


@pytest.mark.browser_context_args(timezone_id="Europe/Oslo", locale="nb-NO")
async def test_table(gateway, page: Page):
    await page.goto(base_url)
    table = page.get_by_role("table")
    table_body = table.locator("#clusters-body")
    await expect(table).to_be_visible()
    columns = 5
    await expect(table.locator("thead").locator("th")).to_have_count(columns)
    await expect(table_body).to_be_empty()
    cluster = await gateway.new_cluster()
    await page.reload()
    # one row
    await expect(table_body.locator("tr")).to_have_count(1)
    # columns match
    await expect(table_body.locator("tr").nth(0).locator("td")).to_have_count(columns)
    # cluster name is in first cell
    await expect(table_body.locator("td").nth(0)).to_contain_text(cluster.name)

    # start time is in last cell
    started_text = await table_body.locator("td").nth(columns - 1).inner_text()
    started_text = started_text.strip()
    dt = datetime.fromtimestamp(
        cluster.scheduler_info["started"], tz=ZoneInfo("Europe/Oslo")
    )
    # localized time
    assert started_text.startswith(f"{dt.day}.{dt.month}.{dt.year}")

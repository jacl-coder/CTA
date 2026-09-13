"""场景切换的账本隔离、失败回滚、重启恢复与旧页面写保护。"""

import asyncio
import json
import socket
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

from cta_risk.bootstrap import create_app
from cta_risk.config.loader import load_settings
from cta_risk.config.models import Settings
from cta_risk.demonstration import DemoController
from cta_risk.marketdata.client import MarketFeed
from cta_risk.settlement.scheduler import SettlementScheduler
from cta_risk.simulator.launcher import SourceProcesses

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[3]


def settings_for(tmp_path: Path) -> Settings:
    raw = yaml.safe_load((ROOT / "configs/default.yaml").read_text())
    with ExitStack() as stack:
        listeners = [stack.enter_context(socket.socket()) for _ in range(2)]
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        for market in (raw["market"], raw["demo"]["fault_market"]):
            for source, listener in zip(market["sources"], listeners, strict=True):
                source["port"] = listener.getsockname()[1]
    raw["demo"]["directory"] = str(tmp_path / "runs")
    raw["ledger"]["database"] = str(tmp_path / "unused.sqlite3")
    path = tmp_path / "default.yaml"
    path.write_text(yaml.safe_dump(raw))
    return load_settings(path)


@pytest.fixture(autouse=True)
def quiet_background(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real ledgers and HTTP contracts, while price progression is controlled by the test.
    for cls in (MarketFeed, SourceProcesses, SettlementScheduler):
        monkeypatch.setattr(cls, "start", AsyncMock())
        monkeypatch.setattr(cls, "close", AsyncMock())


async def auth(client: httpx.AsyncClient, run_id: str) -> dict[str, str]:
    token = (await client.get("/api/session")).json()["token"]
    return {"Authorization": f"Bearer {token}", "X-CTA-Run-Id": run_id}


async def test_switch_isolation_stale_writes_and_restart(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8004"
        ) as client,
    ):
        first = (await client.get("/api/workspace")).json()
        assert first["demo"]["mode"] == "continuous"
        old_run = first["run_id"]
        headers = await auth(client, old_run)
        assert (await client.post("/api/demo/switch", json={"mode": "manual"})).status_code == 409
        assert (
            await client.post(
                "/api/demo/switch", headers={"X-CTA-Run-Id": old_run}, json={"mode": "manual"}
            )
        ).status_code == 401
        response = await client.post("/api/demo/switch", headers=headers, json={"mode": "manual"})
        assert response.status_code == 200
        manual = (await client.get("/api/workspace")).json()
        assert manual["demo"]["mode"] == "manual"
        assert manual["run_id"] != old_run
        assert not manual["system"]["trading_available"]
        # Even a fresh bearer token must not make an old page's same-day order valid.
        stale = await auth(client, old_run)
        assert (
            await client.post(
                "/api/settlement/close", headers=stale, json={"trading_day": "2026-09-09"}
            )
        ).status_code == 409
        assert (
            await client.post("/api/demo/switch", headers=stale, json={"mode": "fault"})
        ).status_code == 409
        headers = await auth(client, manual["run_id"])
        frame = {
            "sequence": 1,
            "trading_day": "2026-09-09",
            "prices": manual["demo"]["initial_prices"],
        }
        assert (
            await client.post("/api/simulation/frames", headers=headers, json=frame)
        ).status_code == 200
        order = {
            "account_id": "A",
            "instrument_id": "SHFE.rb2610",
            "side": "LONG",
            "offset": "OPEN",
            "quantity": 1,
            "request_id": "manual-open",
            "trading_day": "2026-09-09",
        }
        assert (await client.post("/api/orders", headers=headers, json=order)).json()[
            "status"
        ] == "FILLED"
        manual_path = app.state.demo.active.settings.ledger.database
        assert (
            await client.post("/api/demo/switch", headers=headers, json={"mode": "fault"})
        ).status_code == 200
        fault = (await client.get("/api/workspace")).json()
        assert fault["demo"]["mode"] == "fault"
        assert fault["capabilities"]["multi_source"]
        assert len((await client.get("/api/trades")).json()) == 4
        assert manual_path.is_file()
        fault_run = fault["run_id"]
        fault_database = app.state.demo.active.settings.ledger.database
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8004"
        ) as client,
    ):
        restored = (await client.get("/api/workspace")).json()
        assert restored["run_id"] == fault_run
        assert restored["demo"]["mode"] == "fault"
        assert app.state.demo.active.settings.ledger.database == fault_database
        headers = await auth(client, fault_run)
        assert (
            await client.post("/api/demo/switch", headers=headers, json={"mode": "fault"})
        ).status_code == 200
        assert (await client.get("/api/workspace")).json()["run_id"] != fault_run
        assert fault_database.is_file()


async def test_failed_switch_restores_manifest_and_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings_for(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8004"
        ) as client,
    ):
        controller: DemoController = app.state.demo
        previous = controller.active
        original = controller.persist

        def fail_new(active):
            if active != previous:
                raise OSError("simulated disk failure")
            original(active)

        monkeypatch.setattr(controller, "persist", fail_new)
        run_id = (await client.get("/api/workspace")).json()["run_id"]
        response = await client.post(
            "/api/demo/switch", headers=await auth(client, run_id), json={"mode": "manual"}
        )
        assert response.status_code == 503
        assert (await client.get("/api/workspace")).json()["run_id"] == run_id
        assert app.state.ledger.available
        assert json.loads(controller.manifest.read_text())["settings"]["ledger"]["run_id"] == run_id


async def test_writes_during_switch_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings_for(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8004"
        ) as client,
    ):
        controller: DemoController = app.state.demo
        original = controller.stack.aclose
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_close():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(controller.stack, "aclose", delayed_close)
        run_id = (await client.get("/api/workspace")).json()["run_id"]
        headers = await auth(client, run_id)
        switching = asyncio.create_task(
            client.post("/api/demo/switch", headers=headers, json={"mode": "manual"})
        )
        await asyncio.wait_for(entered.wait(), 3)
        try:
            assert (
                await client.post(
                    "/api/settlement/close", headers=headers, json={"trading_day": "2026-09-09"}
                )
            ).status_code == 503
        finally:
            release.set()
        assert (await switching).status_code == 200


async def test_occupied_source_port_keeps_manual_scene(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8004"
        ) as client,
    ):
        first = (await client.get("/api/workspace")).json()["run_id"]
        assert (
            await client.post(
                "/api/demo/switch", headers=await auth(client, first), json={"mode": "manual"}
            )
        ).status_code == 200
        current = (await client.get("/api/workspace")).json()["run_id"]
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", settings.demo.fault_market.sources[0].port))
            occupied.listen()
            response = await client.post(
                "/api/demo/switch", headers=await auth(client, current), json={"mode": "fault"}
            )
        assert response.status_code == 503
        restored = (await client.get("/api/workspace")).json()
        assert restored["run_id"] == current and restored["demo"]["mode"] == "manual"
        assert app.state.ledger.available

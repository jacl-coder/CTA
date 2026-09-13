"""双源跨日恢复、自动调度、报告重试与 HTTP 日结入口。"""

import json
from pathlib import Path

import httpx
import pytest
import yaml

from cta_risk.application.ledger import LedgerService
from cta_risk.bootstrap import create_app
from cta_risk.config.loader import ConfigurationError, load_settings
from cta_risk.domain.models import Offset, Side
from cta_risk.domain.trading import Order
from cta_risk.marketdata.client import MarketFeed
from cta_risk.settlement.scheduler import SettlementScheduler
from cta_risk.simulator.server import create_source_app
from cta_risk.storage.sqlite import SQLiteLedgerStore

pytestmark = [pytest.mark.asyncio, pytest.mark.requirement("SET-01", "SET-02", "SET-03", "MD-03")]
EPOCH = 1_000_000


def settings_for(tmp_path: Path, manual=False):
    root = Path(__file__).resolve().parents[3]
    raw = yaml.safe_load((root / "testdata/configs/settlement-demo.yaml").read_text())
    raw["ledger"]["database"] = "ledger.sqlite3"
    raw["settlement"]["report_dir"] = "reports"
    raw["market"]["managed_sources"] = False
    if manual:
        raw.pop("market")
        raw["settlement"]["auto_settle"] = False
        for item, end in zip(raw["settlement"]["days"], (1, 2), strict=True):
            item["close_sequence"] = end
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw))
    return load_settings(path)


def new_service(settings, now):
    return LedgerService(
        settings.ledger.definition(),
        SQLiteLedgerStore(settings.ledger.database),
        policy=settings.trading.policy(),
        market_plan=settings.market.plan(settings.ledger),
        settlement_plan=settings.settlement.plan(settings.ledger),
        wall_clock=lambda: now[0],
        clock=lambda: now[0] / 1000,
        market_epoch_ms=EPOCH,
    )


class Sources(httpx.AsyncBaseTransport):
    def __init__(self, settings, now):
        self.blocked = False
        self.transports = {
            source.port: httpx.ASGITransport(
                app=create_source_app(settings, source.source_id, EPOCH, clock=lambda: now[0])
            )
            for source in settings.market.sources
        }

    async def handle_async_request(self, request):
        if self.blocked:
            return httpx.Response(503)
        return await self.transports[request.url.port].handle_async_request(request)


async def test_missing_close_data_blocks_settlement_and_next_day_then_recovers(tmp_path):
    settings = settings_for(tmp_path)
    now = [EPOCH]
    service = new_service(settings, now)
    await service.start()
    sources = Sources(settings, now)
    worker = SettlementScheduler(service, settings.settlement)
    async with httpx.AsyncClient(transport=sources) as client:
        feed = MarketFeed(service, settings.market, client)
        await feed.cycle()
        # Both days have elapsed while offline. No next-day price may precede first-day settlement.
        sources.blocked = True
        now[0] = EPOCH + 96 * 250
        await feed.cycle()
        await worker.cycle()
        assert service.trading_state().phase == "CLOSING"
        assert not service.trading_state().reports
        assert not service.trading_available
        await service.close()
        service = new_service(settings, now)
        await service.start()
        assert service.trading_state().phase == "CLOSING"
        worker = SettlementScheduler(service, settings.settlement)
        feed = MarketFeed(service, settings.market, client)
        sources.blocked = False
        try:
            for _ in range(30):
                await feed.cycle()
                await worker.cycle()
                if len(service.trading_state().reports) == 2:
                    break
            state = service.trading_state()
            assert state.phase == "SETTLED" and len(state.reports) == 2
            assert len(state.frames) == 96
            assert [
                (event.account_id, event.frame_sequence)
                for event in state.events
                if event.kind == "CIRCUIT_BREAK"
            ] == [("A", 10), ("B", 32)]
            first, second = map(json.loads, state.reports)
            assert first["accounts"][0]["settlement"]["closing_balance"] == "100292.00"
            assert second["accounts"][0]["settlement"]["closing_balance"] == "99892.00"
            assert all(item["risk_event_count"] == 0 for item in second["accounts"])
            assert {item["trading_day"] for item in (first, second)} == {"2026-09-09", "2026-09-10"}
            assert len(list(settings.settlement.report_dir.glob("*.html"))) == 2
        finally:
            await service.close()
    restored = new_service(settings, now)
    await restored.start()
    try:
        assert restored.trading_state() == state
    finally:
        await restored.close()


async def test_order_at_close_boundary_does_not_wait_for_scheduler(tmp_path):
    settings = settings_for(tmp_path)
    now = [EPOCH + 63 * 250]
    service = new_service(settings, now)
    await service.start()
    async with httpx.AsyncClient(transport=Sources(settings, now)) as client:
        feed = MarketFeed(service, settings.market, client)
        try:
            for _ in range(20):
                await feed.cycle()
                if service.trading_available:
                    break
            assert service.trading_available
            now[0] += 250
            assert service.trading_state().phase == "OPEN"  # scheduler has not yet run
            before = service.trades()
            denied = await service.submit_order(
                Order(
                    "at-close",
                    "C",
                    "CFFEX.IF2609",
                    service.definition.trading_day,
                    Side.LONG,
                    Offset.CLOSE_TODAY,
                    1,
                )
            )
            assert denied.reason == "MARKET_UNAVAILABLE"
            assert service.trades() == before
        finally:
            await service.close()


async def test_http_settlement_permissions_reporting_and_export_retry(tmp_path):
    settings = settings_for(tmp_path, manual=True)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        token = (await client.get("/api/session")).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        day = {"trading_day": "2026-09-09"}
        assert (await client.post("/api/settlement/close", json=day)).status_code == 401
        assert (await client.get("/api/reports/2026-09-09")).status_code == 404
        assert (
            await client.post(
                "/api/settlement/open", headers=headers, json={"trading_day": "2026-09-10"}
            )
        ).status_code == 422
        prices = dict(settings.settlement.plan(settings.ledger).days[0].prices)
        assert (
            await client.post(
                "/api/simulation/frames",
                headers=headers,
                json={
                    **day,
                    "sequence": 1,
                    "prices": {key: str(value) for key, value in prices.items()},
                },
            )
        ).status_code == 200
        assert (
            await client.post("/api/settlement/close", headers=headers, json=day)
        ).status_code == 200
        body = {**day, "prices": {key: str(value) for key, value in prices.items()}}
        # File failure cannot undo an already committed all-account settlement.
        settings.settlement.report_dir.write_text("blocked directory")
        settled = await client.post("/api/settlement/settle", headers=headers, json=body)
        assert settled.status_code == 200 and not settled.json()["duplicate"]
        report = (await client.get("/api/reports/2026-09-09")).json()
        assert (
            await client.post("/api/reports/2026-09-09/export", headers=headers)
        ).status_code == 503
        assert (await client.get("/api/settlement/status")).json()["report_errors"]
        before = app.state.ledger.trading_state()
        assert (await client.post("/api/settlement/settle", headers=headers, json=body)).json()[
            "duplicate"
        ]
        wrong = {**body, "prices": {**body["prices"], "SHFE.rb2610": "3500"}}
        assert (
            await client.post("/api/settlement/settle", headers=headers, json=wrong)
        ).status_code == 409
        assert app.state.ledger.trading_state() == before
        settings.settlement.report_dir.unlink()
        assert (
            await client.post("/api/reports/2026-09-09/export", headers=headers)
        ).status_code == 200
        assert (await client.get("/api/reports/2026-09-09/download/json")).json() == report
        html = await client.get("/api/reports/2026-09-09/download/html")
        assert html.status_code == 200 and "交易风控日报" in html.text
        assert app.state.ledger.trading_state() == before
        assert (await client.get("/api/settlement/status")).json()["report_errors"] == {}


@pytest.mark.parametrize(
    "mutation", ["missing_price", "bad_day", "boundaries", "no_settlement", "auto_without_market"]
)
async def test_bad_settlement_config_rejected_before_database(tmp_path, mutation):
    settings_for(tmp_path)
    path = tmp_path / "settings.yaml"
    raw = yaml.safe_load(path.read_text())
    if mutation == "missing_price":
        raw["settlement"]["days"][0]["prices"].pop("SHFE.rb2610")
    elif mutation == "bad_day":
        raw["settlement"]["days"][1]["trading_day"] = "2026-09-08"
    elif mutation == "boundaries":
        raw["settlement"]["days"][0]["close_sequence"] = 63
    elif mutation == "no_settlement":
        raw.pop("settlement")
    else:
        raw.pop("market")
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigurationError):
        load_settings(path)
    assert not (tmp_path / "ledger.sqlite3").exists()

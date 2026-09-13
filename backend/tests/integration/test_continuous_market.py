"""持续双源的跨日、补数、交易及重启；所有价格均来自实际源协议。"""

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import localcontext
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import Request
from pydantic import ValidationError

from cta_risk.api.workspace import workspace
from cta_risk.application.contracts import ConfigurationMismatch
from cta_risk.application.ledger import LedgerService
from cta_risk.application.trading import encode
from cta_risk.bootstrap import create_app
from cta_risk.config.models import Settings
from cta_risk.domain.market import generate_frame
from cta_risk.domain.models import Offset, Side
from cta_risk.domain.trading import Order
from cta_risk.marketdata.client import MarketFeed
from cta_risk.settlement.scheduler import SettlementScheduler
from cta_risk.simulator.server import create_source_app
from cta_risk.storage.sqlite import SQLiteLedgerStore

EPOCH = 1_000_000
FIRST_DAY = date(2026, 9, 9)
ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.requirement("MD-01", "MD-02", "MD-03", "SET-01", "SET-02")


def raw_settings(tmp_path: Path) -> dict:
    raw = yaml.safe_load((ROOT / "testdata/configs/live-demo.yaml").read_text())
    raw["ledger"]["database"] = tmp_path / "ledger.sqlite3"
    raw["settlement"]["report_dir"] = tmp_path / "reports"
    raw["market"]["managed_sources"] = False
    # Eight frames per accelerated day, deliberately shorter than a price anchor segment.
    raw["market"]["frame_count"] = 8
    raw["market"]["sessions"][0]["close_sequence"] = 8
    raw["settlement"]["days"][0]["close_sequence"] = 8
    raw["market"]["batch_size"] = 4
    return raw


def service_for(settings: Settings, now: list[int]) -> LedgerService:
    assert settings.ledger and settings.trading and settings.market and settings.settlement
    return LedgerService(
        settings.ledger.definition(),
        SQLiteLedgerStore(settings.ledger.database),
        policy=settings.trading.policy(),
        market_plan=settings.market.plan(settings.ledger),
        settlement_plan=settings.settlement.plan(settings.ledger),
        clock=lambda: now[0] / 1000,
        wall_clock=lambda: now[0],
        market_epoch_ms=EPOCH,
    )


class Sources(httpx.AsyncBaseTransport):
    def __init__(self, settings: Settings, now: list[int]):
        assert settings.market
        self.offline = False
        self.first_port = settings.market.sources[0].port
        self.transports = {
            source.port: httpx.ASGITransport(
                app=create_source_app(settings, source.source_id, EPOCH, clock=lambda: now[0])
            )
            for source in settings.market.sources
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.offline and request.url.port == self.first_port:
            return httpx.Response(503)
        assert request.url.port is not None
        return await self.transports[request.url.port].handle_async_request(request)


async def catch_up(feed: MarketFeed, scheduler: SettlementScheduler, target: int) -> None:
    for _ in range(80):
        await feed.cycle()
        await scheduler.cycle()
        if feed.service.source_state().applied_sequence == target:
            return
    raise AssertionError("持续场景未能补齐或跨日")


def test_unbounded_generator_is_reproducible_tick_aligned_and_positive(tmp_path: Path) -> None:
    settings = Settings.model_validate(raw_settings(tmp_path))
    assert settings.market and settings.ledger
    plan = settings.market.plan(settings.ledger)
    source = plan.sources[0]
    frames = [generate_frame(plan, source, EPOCH, seq) for seq in range(1, 130)]
    base = dict(source.points[0].prices)
    ticks = dict(plan.tick_sizes)
    assert len({frame.prices for frame in frames}) > 5
    assert frames[0].prices == source.points[0].prices
    assert [generate_frame(plan, source, EPOCH, f.sequence) for f in reversed(frames)] == list(
        reversed(frames)
    )
    with localcontext() as context:
        context.prec = 2
        assert [generate_frame(plan, source, EPOCH, f.sequence) for f in frames] == frames
    for frame in frames:
        for key, price in frame.prices:
            assert price > 0 and price % ticks[key] == 0
            assert abs(price - base[key]) <= ticks[key] * plan.amplitude_ticks
    assert generate_frame(plan, source, EPOCH, 10001).sequence == 10001
    assert plan.day_at(8) == FIRST_DAY and plan.day_at(9) == FIRST_DAY + timedelta(days=1)
    assert plan.sequence_at(EPOCH, EPOCH + 10000 * plan.interval_ms) == 10001
    assert not plan.completed(EPOCH, EPOCH + 10000 * plan.interval_ms)
    other = replace(plan, seed=plan.seed + 1)
    assert generate_frame(other, source, EPOCH, 17).prices != frames[16].prices


@pytest.mark.asyncio
async def test_source_http_keeps_generating_beyond_old_limit(tmp_path: Path) -> None:
    settings = Settings.model_validate(raw_settings(tmp_path))
    now = [EPOCH + 10000 * 1000]
    app = create_source_app(settings, "source-A", EPOCH, clock=lambda: now[0])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://src"
    ) as c:
        head = (await c.get("/head")).json()
        assert head["sequence"] == 10001 and not head["complete"]
        history = (await c.get("/history?start=9999&end=10001")).json()
        assert [f["sequence"] for f in history["frames"]] == [9999, 10000, 10001]
        assert history["frames"][-1] == head["latest"]
        assert (await c.get("/history?start=10002&end=10002")).status_code == 409


@pytest.mark.asyncio
async def test_continuous_trading_daily_reports_and_restart(tmp_path: Path) -> None:
    settings = Settings.model_validate(raw_settings(tmp_path))
    assert settings.market and settings.settlement
    now = [EPOCH]
    service = service_for(settings, now)
    await service.start()
    try:
        worker = SettlementScheduler(service, settings.settlement)
        async with httpx.AsyncClient(transport=Sources(settings, now)) as client:
            feed = MarketFeed(service, settings.market, client)
            await catch_up(feed, worker, 1)
            assert service.trading_available
            # B has ample product headroom. The opening and next-day closing must persist.
            opened = await service.submit_order(
                Order("live-open", "B", "DCE.i2701", FIRST_DAY, Side.SHORT, Offset.OPEN, 1)
            )
            assert opened.status == "FILLED"
            now[0] = EPOCH + 8 * 1000  # Close day one and generate day two's first frame.
            assert not service.trading_available
            await catch_up(feed, worker, 9)
            assert service.trading_available
            closed_order = Order(
                "live-close",
                "B",
                "DCE.i2701",
                FIRST_DAY + timedelta(days=1),
                Side.SHORT,
                Offset.CLOSE_YESTERDAY,
                1,
            )
            closed = await service.submit_order(closed_order)
            assert closed.status == "FILLED"
            now[0] = EPOCH + 24 * 1000
            await catch_up(feed, worker, 25)
            assert service.trading_state().trading_day == FIRST_DAY + timedelta(days=3)
            assert service.trading_available and not service.source_status()["completed"]
            assert len(service.trading_state().reports) == 3
            assert len(service.source_state().frames) == 2  # Applied raw history stays in SQLite.
            assert all(s["received_count"] == 25 for s in service.source_status()["sources"])
            reports = service.trading_state().reports
            for offset, payload in enumerate(reports):
                report = json.loads(payload)
                assert report["trading_day"] == str(FIRST_DAY + timedelta(days=offset))
                assert report["close_sequence"] == (offset + 1) * 8
                assert report["prices"] == {
                    "SHFE.rb2610": "3500",
                    "DCE.i2701": "800",
                    "CFFEX.IF2609": "4000",
                }
                exported = settings.settlement.report_dir / f"risk-{report['trading_day']}.json"
                assert json.loads(exported.read_text()) == report
            original = service.trading_state()
            # Workbench must expose current and next day, rather than just the initial template.
            app = create_app(settings)
            app.state.ledger = service
            app.state.instance_id = "continuous-test"
            app.state.settlement_scheduler = worker
            snapshot = await workspace(Request({"type": "http", "app": app}))
            assert [d.trading_day for d in snapshot.settlement_days] == ["2026-09-12", "2026-09-13"]
    finally:
        await service.close()
    restored = service_for(settings, now)
    await restored.start()
    try:
        assert restored.trading_state() == original
        assert not restored.trading_available
        worker = SettlementScheduler(restored, settings.settlement)
        async with httpx.AsyncClient(transport=Sources(settings, now)) as client:
            feed = MarketFeed(restored, settings.market, client)
            await catch_up(feed, worker, 25)
            assert not restored.trading_available  # A restart waits for a fresh live frame.
            assert (await restored.submit_order(closed_order)).duplicate
            now[0] += 1000
            await catch_up(feed, worker, 26)
            assert restored.trading_available
            assert restored.source_state().applied_sequence == 26
            assert restored.trading_state().reports == reports
            assert len(restored.source_state().frames) == 2
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_outage_across_close_recovers_identical_risk_and_reports(tmp_path: Path) -> None:
    async def run(directory: Path, offline: bool):
        raw = raw_settings(directory)
        # Small thresholds deliberately exercise event history during the missing interval.
        raw["trading"]["warning_ratio"] = "0.00001"
        raw["trading"]["loss_ratio"] = "0.00002"
        settings = Settings.model_validate(raw)
        assert settings.market and settings.settlement
        now = [EPOCH]
        service = service_for(settings, now)
        await service.start()
        transport = Sources(settings, now)
        try:
            worker = SettlementScheduler(service, settings.settlement)
            async with httpx.AsyncClient(transport=transport) as client:
                feed = MarketFeed(service, settings.market, client)
                await catch_up(feed, worker, 1)
                transport.offline = offline
                for seq in range(2, 24):
                    now[0] = EPOCH + (seq - 1) * 1000
                    if offline:
                        await feed.cycle()
                        await worker.cycle()
                        assert service.source_state().applied_sequence == 1
                        assert not service.trading_available
                        assert not service.trading_state().reports
                        if seq == 12:
                            await service.close()
                            service = service_for(settings, now)
                            await service.start()
                            worker = SettlementScheduler(service, settings.settlement)
                            feed = MarketFeed(service, settings.market, client)
                    else:
                        await catch_up(feed, worker, seq)
                transport.offline = False
                now[0] = EPOCH + 23 * 1000
                await catch_up(feed, worker, 24)
                assert service.trading_available
                assert any(e.kind == "CIRCUIT_BREAK" for e in service.trading_state().events)
                return service.trading_state()
        finally:
            await service.close()

    baseline = await run(tmp_path / "normal", False)
    recovered = await run(tmp_path / "recovered", True)
    assert recovered == baseline  # Includes every historical risk event and both frozen reports.


@pytest.mark.asyncio
async def test_seed_change_rejects_existing_run(tmp_path: Path) -> None:
    raw = raw_settings(tmp_path)
    settings = Settings.model_validate(raw)
    service = service_for(settings, [EPOCH])
    await service.start()
    await service.close()
    raw["market"]["seed"] += 1
    with pytest.raises(ConfigurationMismatch):
        await service_for(Settings.model_validate(raw), [EPOCH]).start()


@pytest.mark.parametrize("mutation", ["no_settlement", "finite", "manual", "two_days", "negative"])
def test_invalid_continuous_config_is_rejected(tmp_path: Path, mutation: str) -> None:
    raw = raw_settings(tmp_path)
    if mutation == "no_settlement":
        raw.pop("settlement")
    elif mutation == "finite":
        raw["settlement"]["repeat_daily"] = False
    elif mutation == "manual":
        raw["settlement"]["auto_settle"] = False
    elif mutation == "two_days":
        raw["settlement"]["days"].append(
            {
                **raw["settlement"]["days"][0],
                "trading_day": "2026-09-10",
                "close_sequence": 16,
            }
        )
    else:
        raw["market"]["sources"][0]["points"][0]["prices"]["SHFE.rb2610"] = "1"
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


def test_finite_plan_serialization_stays_compatible(tmp_path: Path) -> None:
    raw = yaml.safe_load((ROOT / "testdata/configs/settlement-demo.yaml").read_text())
    raw["ledger"]["database"] = tmp_path / "ledger.sqlite3"
    raw["settlement"]["report_dir"] = tmp_path / "reports"
    settings = Settings.model_validate(raw)
    assert settings.market and settings.settlement and settings.ledger
    plan = settings.market.plan(settings.ledger)
    assert "continuous" not in json.loads(plan.to_json())
    assert set(json.loads(encode(settings.settlement.plan(settings.ledger)))) == {"days"}
    assert plan.sequence_at(EPOCH, EPOCH + 1000000) == 96
    assert plan.completed(EPOCH, EPOCH + 1000000)

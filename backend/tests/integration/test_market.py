"""MD-01/02/03：两个独立 HTTP 应用、缺口补拉与风险顺序的一致性。"""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import yaml

from cta_risk.application.contracts import ConfigurationMismatch, LedgerUnavailable, StorageError
from cta_risk.application.ledger import LedgerService
from cta_risk.application.market import ApplyMarketFrame, SourceBatch
from cta_risk.config.loader import ConfigurationError, load_settings
from cta_risk.config.market import OutageSettings
from cta_risk.config.models import Settings
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.market import generate_frame
from cta_risk.domain.models import Offset, Side
from cta_risk.domain.trading import Order
from cta_risk.marketdata.client import MarketFeed
from cta_risk.simulator.server import create_source_app
from cta_risk.storage.sqlite import SQLiteLedgerStore

pytestmark = [pytest.mark.asyncio, pytest.mark.requirement("MD-01", "MD-02", "MD-03", "RISK-04")]
EPOCH = 1_000_000


@pytest.fixture
def market_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[3]
    raw = yaml.safe_load((root / "configs/market-demo.yaml").read_text())
    raw["ledger"]["database"] = "ledger.sqlite3"
    raw["market"]["managed_sources"] = False
    raw["market"]["batch_size"] = 4
    path = tmp_path / "market.yaml"
    path.write_text(yaml.safe_dump(raw))
    return load_settings(path)


def variant(settings: Settings, database: Path, faults: set[str]) -> Settings:
    assert settings.market and settings.ledger
    sources = tuple(
        item.model_copy(update={"outages": item.outages if item.source_id in faults else ()})
        for item in settings.market.sources
    )
    return settings.model_copy(
        update={
            "ledger": settings.ledger.model_copy(update={"database": database}),
            "market": settings.market.model_copy(update={"sources": sources}),
        }
    )


def service_for(settings: Settings, now: list[int], store=None) -> LedgerService:
    assert settings.ledger and settings.trading and settings.market
    return LedgerService(
        settings.ledger.definition(),
        store or SQLiteLedgerStore(settings.ledger.database),
        policy=settings.trading.policy(),
        market_plan=settings.market.plan(settings.ledger),
        clock=lambda: now[0] / 1000,
        wall_clock=lambda: now[0],
        market_epoch_ms=EPOCH,
    )


class RoutedSources(httpx.AsyncBaseTransport):
    def __init__(self, settings: Settings, now: list[int]):
        assert settings.market
        self.transports = {
            item.port: httpx.ASGITransport(
                app=create_source_app(settings, item.source_id, EPOCH, clock=lambda: now[0])
            )
            for item in settings.market.sources
        }
        self.requests: list[tuple[int, str]] = []
        self.corrupt = ""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        port = request.url.port
        assert port is not None
        self.requests.append((port, request.url.path))
        if self.corrupt == "timeout":
            raise httpx.ReadTimeout("injected timeout", request=request)
        response = await self.transports[port].handle_async_request(request)
        if self.corrupt and response.status_code == 200:
            raw = json.loads(await response.aread())
            if self.corrupt == "identity":
                raw["run_id"] = "wrong-run"
            elif self.corrupt == "hash" and request.url.path == "/head" and raw["latest"]:
                raw["latest"]["digest"] = "wrong"
            elif self.corrupt == "history" and request.url.path == "/history":
                raw["frames"] = raw["frames"][:-1]
            return httpx.Response(200, json=raw)
        return response


async def catch_up(feed: MarketFeed, sequence: int) -> None:
    for _ in range(50):
        await feed.cycle()
        if feed.service.source_state().applied_sequence == sequence:
            return
    raise AssertionError("未能补齐行情")


async def run_scenario(settings: Settings):
    assert settings.market and settings.ledger
    now = [EPOCH]
    service = service_for(settings, now)
    await service.start()
    transport = RoutedSources(settings, now)
    async with httpx.AsyncClient(transport=transport) as client:
        feed = MarketFeed(service, settings.market, client)
        try:
            for sequence in range(1, settings.market.frame_count + 1):
                now[0] = EPOCH + (sequence - 1) * settings.market.interval_ms
                outage = any(
                    fault.start <= sequence <= fault.end
                    for item in settings.market.sources
                    for fault in item.outages
                )
                if outage:
                    await feed.cycle()
                    assert not service.trading_available
                    before = service.trades()
                    result = await service.submit_order(
                        Order(
                            f"during-{sequence}",
                            "A",
                            "SHFE.rb2610",
                            service.definition.trading_day,
                            Side.LONG,
                            Offset.CLOSE_TODAY,
                            1,
                        )
                    )
                    assert result.reason == "MARKET_UNAVAILABLE"
                    assert service.trades() == before
                else:
                    await catch_up(feed, sequence)
                    assert service.trading_available
            # Repeated heads/history do not advance business frames or repeat transitions.
            before = service.trading_state()
            await feed.cycle()
            await feed.cycle()
            assert service.trading_state() == before
            assert service.source_status()["duplicate_frames"] > 0
            assert len(service.source_state().frames) == 2 * settings.market.frame_count
            stored = service.source_state()
            now[0] += settings.market.interval_ms
            assert not service.trading_available
            assert service.source_status()["completed"]
            return before, stored, transport.requests
        finally:
            await service.close()


@pytest.mark.parametrize("faults", [{"source-A"}, {"source-B"}, {"source-A", "source-B"}])
async def test_recovery_matches_continuous_risk_and_ledger(
    market_settings: Settings, tmp_path: Path, faults: set[str]
) -> None:
    baseline = variant(market_settings, tmp_path / "continuous.sqlite3", set())
    interrupted = variant(market_settings, tmp_path / "interrupted.sqlite3", faults)
    normal, _, _ = await run_scenario(baseline)
    recovered, stored, requests = await run_scenario(interrupted)
    assert recovered.ledger == normal.ledger
    assert recovered.frames == normal.frames
    assert recovered.risks == normal.risks
    assert recovered.events == normal.events
    breakers = [item for item in recovered.events if item.kind == "CIRCUIT_BREAK"]
    assert [(item.account_id, item.frame_sequence) for item in breakers] == [("A", 10), ("B", 32)]
    metadata = {item.event_id: item for item in stored.observations}
    for event in breakers:
        observation = metadata[event.event_id]
        source = "source-A" if event.account_id == "A" else "source-B"
        assert observation.recovered is (source in faults)
        assert observation.occurred_ms == EPOCH + (event.frame_sequence - 1) * 250
        assert observation.detected_ms >= observation.occurred_ms
    assert {port for port, _ in requests} == {8101, 8102}
    assert any(path == "/history" for _, path in requests)


async def test_partial_buffer_restart_and_historical_frames_stay_untradable(
    market_settings: Settings,
) -> None:
    assert market_settings.market
    now = [EPOCH]
    service = service_for(market_settings, now)
    await service.start()
    async with httpx.AsyncClient(transport=RoutedSources(market_settings, now)) as client:
        feed = MarketFeed(service, market_settings.market, client)
        await feed.cycle()
        now[0] = EPOCH + 19 * 250
        await (
            feed.cycle()
        )  # A disconnected, B raw frame 20 buffered, complete applied frame stays 1.
        assert service.source_state().applied_sequence == 1
        assert max(item.sequence for item in service.source_state().frames) == 20
    await service.close()
    service = service_for(market_settings, now)
    await service.start()
    try:
        assert not service.trading_available
        assert service.source_state().epoch_ms == EPOCH
        async with httpx.AsyncClient(transport=RoutedSources(market_settings, now)) as client:
            feed = MarketFeed(service, market_settings.market, client)
            now[0] = EPOCH + 20 * 250
            await feed.cycle()
            assert 1 < service.source_state().applied_sequence < 21
            assert not service.trading_available
            result = await service.submit_order(
                Order(
                    "during-backfill",
                    "A",
                    "SHFE.rb2610",
                    service.definition.trading_day,
                    Side.LONG,
                    Offset.OPEN,
                    1,
                )
            )
            assert result.reason == "MARKET_UNAVAILABLE"
            await catch_up(feed, 21)
            assert service.trading_available and service.trading_state().risks[0].circuit_broken
            with pytest.raises(LedgerUnavailable, match="手工"):
                await service.publish_frame(service.trading_state().frames[-1])
        expected = service.trading_state(), service.source_state()
    finally:
        await service.close()
    restored = service_for(market_settings, now)
    await restored.start()
    try:
        assert (restored.trading_state(), restored.source_state()) == expected
        assert not restored.trading_available
    finally:
        await restored.close()


@pytest.mark.parametrize("corrupt", ["identity", "hash", "timeout", "history"])
async def test_source_errors_never_skip_gaps(
    market_settings: Settings, tmp_path: Path, corrupt: str
) -> None:
    settings = variant(market_settings, tmp_path / "invalid.sqlite3", set())
    assert settings.market
    now = [EPOCH]
    service = service_for(settings, now)
    await service.start()
    transport = RoutedSources(settings, now)
    async with httpx.AsyncClient(transport=transport) as client:
        feed = MarketFeed(service, settings.market, client)
        try:
            await feed.cycle()
            now[0] += 20 * 250
            transport.corrupt = corrupt
            if corrupt == "history":
                with pytest.raises(AccountingError, match="补拉"):
                    await feed.cycle()
            else:
                await feed.cycle()
            assert service.source_state().applied_sequence == 1
            assert not service.trading_available
            transport.corrupt = ""
            await catch_up(feed, 21)
            assert service.trading_state().risks[0].circuit_broken
        finally:
            await service.close()


class MarketFailure(SQLiteLedgerStore):
    fail = False

    def _update_market_cursor(self, run_id, sequence, observations):
        super()._update_market_cursor(run_id, sequence, observations)
        if self.fail:
            raise sqlite3.OperationalError("injected after risk and market cursor")


def accounting_tables(path: Path):
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in (
                "account_books",
                "trades",
                "trading_runs",
                "trading_journal",
                "market_runs",
                "market_event_observations",
            )
        }


async def test_applied_cursor_and_risk_commit_atomically(
    market_settings: Settings, tmp_path: Path
) -> None:
    settings = variant(market_settings, tmp_path / "fault.sqlite3", set())
    assert settings.ledger and settings.market
    now = [EPOCH]
    store = MarketFailure(settings.ledger.database)
    service = service_for(settings, now, store)
    await service.start()
    async with httpx.AsyncClient(transport=RoutedSources(settings, now)) as client:
        feed = MarketFeed(service, settings.market, client)
        for sequence in range(1, 10):
            now[0] = EPOCH + (sequence - 1) * 250
            await feed.cycle()
        before = accounting_tables(store.path)
        store.fail = True
        now[0] += 250
        with pytest.raises(LedgerUnavailable):
            await feed.cycle()
        assert accounting_tables(store.path) == before
    await service.close()
    restored = service_for(settings, now)
    await restored.start()
    try:
        assert restored.source_state().applied_sequence == 9
        async with httpx.AsyncClient(transport=RoutedSources(settings, now)) as client:
            await MarketFeed(restored, settings.market, client).cycle()
        assert restored.source_state().applied_sequence == 10
        assert sum(item.kind == "CIRCUIT_BREAK" for item in restored.trading_state().events) == 1
    finally:
        await restored.close()


async def test_no_partial_or_future_frame_is_applied(market_settings: Settings) -> None:
    now = [EPOCH]
    service = service_for(market_settings, now)
    await service.start()
    try:
        plan = service.market_plan
        assert plan is not None
        first = generate_frame(plan, plan.sources[0], EPOCH, 1)
        await service.market_command(SourceBatch((first,)))
        with pytest.raises(AccountingError, match="缺少"):
            await service.market_command(ApplyMarketFrame(False))
        with pytest.raises(AccountingError, match="未来"):
            await service.market_command(
                SourceBatch((generate_frame(plan, plan.sources[0], EPOCH, 2),))
            )
        with pytest.raises(AccountingError, match="场景"):
            await service.market_command(SourceBatch((replace(first, run_id="wrong"),)))
        assert service.source_state().applied_sequence == 0
        assert not service.trading_state().events
    finally:
        await service.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM market_frames WHERE source_id='source-A' AND sequence=10",
        "UPDATE market_runs SET applied_sequence=0",
        "UPDATE market_frames SET digest='changed' WHERE sequence=1",
        "DELETE FROM market_event_observations",
    ],
)
async def test_market_corruption_refuses_restore(
    market_settings: Settings, tmp_path: Path, sql: str
) -> None:
    settings = variant(market_settings, tmp_path / "corrupt.sqlite3", set())
    assert settings.ledger and settings.market
    now = [EPOCH + 9 * 250]
    service = service_for(settings, now)
    await service.start()
    async with httpx.AsyncClient(transport=RoutedSources(settings, now)) as client:
        await catch_up(MarketFeed(service, settings.market, client), 10)
    await service.close()
    with sqlite3.connect(settings.ledger.database) as connection:
        connection.execute(sql)
    with pytest.raises(StorageError):
        await service_for(settings, now).start()


async def test_plan_is_frozen_but_outages_are_transport_settings(
    market_settings: Settings, tmp_path: Path
) -> None:
    assert market_settings.market and market_settings.ledger and market_settings.trading
    now = [EPOCH]
    service = service_for(market_settings, now)
    await service.start()
    await service.close()
    normal = variant(market_settings, market_settings.ledger.database, set())
    restored = service_for(normal, now)
    await restored.start()
    await restored.close()
    changed = normal.model_copy(
        update={"market": normal.market.model_copy(update={"interval_ms": 100})}
    )
    with pytest.raises(ConfigurationMismatch):
        await service_for(changed, now).start()
    with pytest.raises(ConfigurationMismatch):
        await LedgerService(
            normal.ledger.definition(),
            SQLiteLedgerStore(normal.ledger.database),
            policy=normal.trading.policy(),
        ).start()


@pytest.mark.parametrize(
    "mutation", ["overlap", "missing", "no_first", "tick", "port", "no_trading"]
)
async def test_invalid_market_config_rejected_without_database(
    tmp_path: Path, mutation: str
) -> None:
    root = Path(__file__).resolve().parents[3]
    raw = yaml.safe_load((root / "configs/market-demo.yaml").read_text())
    raw["ledger"]["database"] = "never-created.sqlite3"
    sources = raw["market"]["sources"]
    if mutation == "overlap":
        sources[1]["instruments"].append("SHFE.rb2610")
    elif mutation == "missing":
        sources.pop()
    elif mutation == "no_first":
        sources[0]["points"][0]["sequence"] = 2
    elif mutation == "tick":
        sources[0]["points"][0]["prices"]["SHFE.rb2610"] = "3500.1"
    elif mutation == "port":
        sources[1]["port"] = sources[0]["port"]
    else:
        raw.pop("trading")
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigurationError):
        load_settings(path)
    assert not (tmp_path / "never-created.sqlite3").exists()


async def test_source_history_is_bounded_and_rejects_unavailable_ranges(
    market_settings: Settings, tmp_path: Path
) -> None:
    settings = variant(market_settings, tmp_path / "unused.sqlite3", set())
    now = [EPOCH - 1]
    app = create_source_app(settings, "source-A", EPOCH, clock=lambda: now[0])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://source"
    ) as c:
        head = (await c.get("/head")).json()
        assert head["sequence"] == 0 and head["latest"] is None
        assert (await c.get("/history?start=1&end=1")).status_code == 409
        now[0] = EPOCH + 9 * 250
        history = (await c.get("/history?start=2&end=10&limit=3")).json()
        assert [item["sequence"] for item in history["frames"]] == [2, 3, 4]
        assert history["end"] == 4 and history["next_sequence"] == 5
        for query in ("start=0&end=1", "start=1&end=2&limit=257"):
            assert (await c.get("/history?" + query)).status_code == 422
        for query in ("start=10&end=9", "start=1&end=11"):
            assert (await c.get("/history?" + query)).status_code == 409


async def test_outage_in_last_frame_recovers_after_scenario_ends(
    market_settings: Settings, tmp_path: Path
) -> None:
    settings = variant(market_settings, tmp_path / "last.sqlite3", set())
    assert settings.market
    sources = tuple(
        item.model_copy(update={"outages": (OutageSettings(start=1, end=64),)})
        for item in settings.market.sources
    )
    settings = settings.model_copy(
        update={"market": settings.market.model_copy(update={"sources": sources})}
    )
    now = [EPOCH + 63 * 250]
    service = service_for(settings, now)
    await service.start()
    try:
        async with httpx.AsyncClient(transport=RoutedSources(settings, now)) as client:
            feed = MarketFeed(service, settings.market, client)
            await feed.cycle()
            assert service.source_state().applied_sequence == 0
            now[0] += 250
            await catch_up(feed, 64)
            assert service.source_status()["completed"] and not service.trading_available
            assert [
                (item.account_id, item.frame_sequence)
                for item in service.trading_state().events
                if item.kind == "CIRCUIT_BREAK"
            ] == [("A", 10), ("B", 32)]
    finally:
        await service.close()

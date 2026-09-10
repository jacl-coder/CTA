"""原题 RISK-04：从真实下单入口验证阈值熔断、拒单、有效平仓及恢复。"""

import asyncio
import sqlite3
import threading
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from cta_risk.application.contracts import (
    ConfigurationMismatch,
    JournalEntry,
    LedgerUnavailable,
    StorageError,
)
from cta_risk.application.ledger import LedgerService
from cta_risk.application.trading import risk_views
from cta_risk.bootstrap import create_app
from cta_risk.config.models import Settings
from cta_risk.config.trading import TradingSettings
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import Offset, Side
from cta_risk.domain.trading import CommandConflict, Order, PriceFrame, ProductLimit, RiskPolicy
from cta_risk.storage.sqlite import SQLiteLedgerStore

D = Decimal
DAY = date(2026, 9, 9)
POLICY = RiskPolicy(D("0.02"), D("0.03"), D("2000000"))
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.requirement("RISK-01", "RISK-02", "RISK-03", "RISK-04"),
]


def frame(sequence: int, rb: str = "3500", iron: str = "800") -> PriceFrame:
    return PriceFrame(
        sequence, DAY, (("CFFEX.IF2609", D("4000")), ("DCE.i2701", D(iron)), ("SHFE.rb2610", D(rb)))
    )


def order(
    key: str,
    account: str = "A",
    *,
    quantity: int = 1,
    offset: Offset = Offset.OPEN,
    instrument: str = "SHFE.rb2610",
    side: Side = Side.LONG,
) -> Order:
    return Order(key, account, instrument, DAY, side, offset, quantity)


def make_service(
    settings: Settings, *, policy: RiskPolicy = POLICY, store=None, clock=None
) -> LedgerService:
    assert settings.ledger is not None
    options = {"clock": clock} if clock is not None else {}
    return LedgerService(
        settings.ledger.definition(),
        store or SQLiteLedgerStore(settings.ledger.database),
        policy=policy,
        **options,
    )


async def test_exact_threshold_rebound_close_and_account_isolation(demo_settings: Settings) -> None:
    service = make_service(demo_settings)
    await service.start()
    try:
        assert not service.trading_available
        await service.publish_frame(frame(1, "3351"))
        a = risk_views(service.trading_state(), True)[0]
        assert a.floating_pnl == D("-2980") and a.warning and not a.circuit_broken
        await service.publish_frame(frame(2, "3350"))
        a = risk_views(service.trading_state(), True)[0]
        assert (a.floating_pnl, a.equity, a.margin) == (D("-3000"), D("97092"), D("6700"))
        assert a.circuit_broken and not a.opening_allowed
        before = service.accounts(), service.trades()
        denied = await service.submit_order(order("blocked"))
        assert denied.status == "REJECTED" and denied.reason == "CIRCUIT_BROKEN"
        assert (service.accounts(), service.trades()) == before
        await service.publish_frame(frame(3, "3450"))
        assert (await service.submit_order(order("rebound"))).reason == "CIRCUIT_BROKEN"
        close = await service.submit_order(order("close", offset=Offset.CLOSE_TODAY))
        assert close.status == "FILLED" and close.price == D("3450") and close.fee == D("2")
        assert service.account("A").book.realized_pnl == D("-400")
        assert service.account("A").book.fees == D("10")
        assert service.positions("A")[0].quantity == 1
        assert service.trading_state().risks[0].circuit_broken
        b = await service.submit_order(
            order("B-open", "B", instrument="DCE.i2701", side=Side.SHORT)
        )
        assert b.status == "FILLED"
        assert service.account("B").book.fees == D("9")
        events = service.trading_state().events
        assert sum(event.kind == "CIRCUIT_BREAK" for event in events) == 1
    finally:
        await service.close()


async def test_restart_retains_breaker_results_and_requires_new_price(
    demo_settings: Settings,
) -> None:
    service = make_service(demo_settings)
    await service.start()
    await service.publish_frame(frame(1))
    filled = await service.submit_order(order("filled"))
    await service.publish_frame(frame(2, "3400"))  # 3 手 * 100 * 10 = 3000
    denied = await service.submit_order(order("denied"))
    expected = service.trading_state()
    await service.close()
    restored = make_service(demo_settings)
    await restored.start()
    try:
        assert restored.trading_state() == expected
        assert not restored.trading_available
        assert (await restored.submit_order(order("filled"))) == replace(filled, duplicate=True)
        assert (await restored.submit_order(order("denied"))) == replace(denied, duplicate=True)
        assert (await restored.publish_frame(frame(2, "3400"))).duplicate
        assert not restored.trading_available  # 重发旧帧不会恢复新鲜度。
        assert (await restored.submit_order(order("no-new-price"))).reason == "MARKET_UNAVAILABLE"
        await restored.publish_frame(frame(3))
        assert restored.trading_available
        assert (await restored.submit_order(order("after-recovery"))).reason == "CIRCUIT_BROKEN"
        assert sum(event.kind == "CIRCUIT_BREAK" for event in restored.trading_state().events) == 1
        with pytest.raises(CommandConflict):
            await restored.submit_order(order("filled", quantity=2))
        assert restored.available
    finally:
        await restored.close()


async def test_projected_exposure_serializes_concurrent_opens(demo_settings: Settings) -> None:
    policy = replace(POLICY, product_limits=(ProductLimit("A", "SHFE.rb", D("105000")),))
    service = make_service(demo_settings, policy=policy)
    await service.start()
    try:
        await service.publish_frame(frame(1))
        results = await asyncio.gather(
            *(service.submit_order(order(f"open-{i}")) for i in range(2))
        )
        assert [result.status for result in results] == ["FILLED", "REJECTED"]
        assert results[1].reason == "PROJECTED_EXPOSURE_LIMIT"
        assert service.positions("A")[0].quantity == 3
        assert service.account("A").book.fees == D("10")
        await service.publish_frame(frame(2, "3501"))
        assert (await service.submit_order(order("current-limit"))).reason == "EXPOSURE_LIMIT"
        assert (
            await service.submit_order(order("reduce", offset=Offset.CLOSE_TODAY))
        ).status == "FILLED"
        assert not service.trading_state().risks[0].restricted_products
        kinds = [event.kind for event in service.trading_state().events]
        assert kinds == ["EXPOSURE_LIMIT_ENTER", "EXPOSURE_LIMIT_EXIT"]
    finally:
        await service.close()


async def test_gross_exposure_does_not_net_long_and_short(demo_settings: Settings) -> None:
    policy = replace(POLICY, product_limits=(ProductLimit("A", "SHFE.rb", D("100000")),))
    service = make_service(demo_settings, policy=policy)
    await service.start()
    try:
        await service.publish_frame(frame(1))
        result = await service.submit_order(order("short", side=Side.SHORT))
        assert result.reason == "PROJECTED_EXPOSURE_LIMIT"
        assert service.positions("A")[0].quantity == 2
    finally:
        await service.close()


async def test_insufficient_margin_and_invalid_close_leave_no_trade(
    demo_settings: Settings,
) -> None:
    service = make_service(
        demo_settings, policy=replace(POLICY, default_product_limit=D("100000000"))
    )
    await service.start()
    try:
        await service.publish_frame(frame(1))
        before = service.accounts(), service.trades()
        assert (
            await service.submit_order(order("margin", quantity=30))
        ).reason == "INSUFFICIENT_MARGIN"
        assert (
            await service.submit_order(order("overclose", quantity=3, offset=Offset.CLOSE_TODAY))
        ).reason == "INVALID_POSITION"
        assert (
            await service.submit_order(order("wrong-day", offset=Offset.CLOSE_YESTERDAY))
        ).reason == "INVALID_POSITION"
        assert (service.accounts(), service.trades()) == before
        raw = service.trades()[0].fill
        with pytest.raises(LedgerUnavailable, match="禁止"):
            await service.record_fill(raw, "bypass")
    finally:
        await service.close()


async def test_stale_gap_conflict_and_duplicate_price_handling(demo_settings: Settings) -> None:
    now = [100.0]
    service = make_service(demo_settings, clock=lambda: now[0])
    await service.start()
    try:
        no_price = await service.submit_order(order("early"))
        assert no_price.reason == "MARKET_UNAVAILABLE"
        await service.publish_frame(frame(1))
        assert (await service.submit_order(order("early"))).duplicate
        assert (await service.submit_order(order("early"))).status == "REJECTED"
        now[0] += 31
        assert not service.trading_available
        assert (await service.publish_frame(frame(1))).duplicate
        assert not service.trading_available
        assert (
            await service.submit_order(order("stale-close", offset=Offset.CLOSE_TODAY))
        ).reason == "MARKET_UNAVAILABLE"
        with pytest.raises(AccountingError, match="不连续"):
            await service.publish_frame(frame(3))
        assert len(service.trading_state().frames) == 1
        await service.publish_frame(frame(2))
        assert service.trading_available
        with pytest.raises(CommandConflict):
            await service.publish_frame(frame(2, "3350"))
        assert not service.trading_available
        await service.publish_frame(frame(3, "3350"))
        assert service.trading_state().risks[0].circuit_broken
        count = len(service.trading_state().events)
        await service.publish_frame(frame(3, "3350"))
        assert len(service.trading_state().events) == count
    finally:
        await service.close()


@pytest.mark.parametrize(
    "bad",
    [
        PriceFrame(1, DAY, (("SHFE.rb2610", D("3350")),)),
        replace(frame(1), trading_day=date(2026, 9, 10)),
        frame(1, "3350.5"),
    ],
)
async def test_invalid_frames_do_not_partially_update_risk(
    demo_settings: Settings, bad: PriceFrame
) -> None:
    service = make_service(demo_settings)
    await service.start()
    try:
        before = service.trading_state()
        with pytest.raises(AccountingError):
            await service.publish_frame(bad)
        assert service.trading_state() == before
        assert service.available and not service.trading_available
    finally:
        await service.close()


class FaultStore(SQLiteLedgerStore):
    fail = False

    def _insert_journal(self, run_id: str, entry: JournalEntry) -> None:
        super()._insert_journal(run_id, entry)
        if self.fail:
            raise sqlite3.OperationalError(
                "injected failure after trade, positions and risk journal"
            )


def database_snapshot(path: Path) -> dict[str, list]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in (
                "account_books",
                "trades",
                "position_lots",
                "close_allocations",
                "trading_journal",
                "trading_runs",
            )
        }


@pytest.mark.parametrize("kind", ["order", "frame"])
async def test_risk_and_trade_transaction_roll_back_together(
    demo_settings: Settings, kind: str
) -> None:
    assert demo_settings.ledger is not None
    store = FaultStore(demo_settings.ledger.database)
    service = make_service(demo_settings, store=store)
    await service.start()
    await service.publish_frame(frame(1))
    before = database_snapshot(store.path)
    store.fail = True
    try:
        with pytest.raises(LedgerUnavailable):
            if kind == "order":
                await service.submit_order(order("rollback", offset=Offset.CLOSE_TODAY))
            else:
                await service.publish_frame(frame(2, "3350"))
        assert database_snapshot(store.path) == before
        assert not service.trading_available
    finally:
        await service.close()
    restored = make_service(demo_settings)
    await restored.start()
    try:
        assert not restored.trading_state().risks[0].circuit_broken
        assert len(restored.trading_state().orders) == 0
        await restored.publish_frame(frame(2, "3350" if kind == "frame" else "3500"))
        if kind == "frame":
            assert restored.trading_state().risks[0].circuit_broken
        else:
            assert (
                await restored.submit_order(order("rollback", offset=Offset.CLOSE_TODAY))
            ).status == "FILLED"
    finally:
        await restored.close()


class PausedTradingStore(SQLiteLedgerStore):
    pause = False

    def __init__(self, path: Path):
        super().__init__(path)
        self.entered = threading.Event()
        self.release = threading.Event()

    def _insert_journal(self, run_id: str, entry: JournalEntry) -> None:
        super()._insert_journal(run_id, entry)
        if self.pause:
            self.entered.set()
            if not self.release.wait(5):
                raise sqlite3.OperationalError("test timeout")


async def test_cancelled_order_still_commits_once(demo_settings: Settings) -> None:
    assert demo_settings.ledger is not None
    store = PausedTradingStore(demo_settings.ledger.database)
    service = make_service(demo_settings, store=store)
    await service.start()
    await service.publish_frame(frame(1))
    store.pause = True
    pending = asyncio.create_task(service.submit_order(order("cancelled")))
    try:
        async with asyncio.timeout(3):
            while not store.entered.is_set():
                await asyncio.sleep(0.001)
        assert len(service.trading_state().orders) == 0
        assert service.account("A").revision == 2
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        store.release.set()
        result = await service.submit_order(order("cancelled"))
        assert result.duplicate and result.status == "FILLED"
        assert service.account("A").revision == 3
        assert service.account("A").book.fees == D("10")
    finally:
        store.release.set()
        await service.close()


async def test_trading_policy_cannot_change_or_be_disabled_on_restart(
    demo_settings: Settings,
) -> None:
    assert demo_settings.ledger is not None
    service = make_service(demo_settings)
    await service.start()
    await service.close()
    with pytest.raises(ConfigurationMismatch):
        await make_service(demo_settings, policy=replace(POLICY, loss_ratio=D("0.04"))).start()
    with pytest.raises(ConfigurationMismatch):
        await LedgerService(
            demo_settings.ledger.definition(), SQLiteLedgerStore(demo_settings.ledger.database)
        ).start()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE trading_journal SET outcome_json='{}' WHERE revision=2",
        "DELETE FROM trading_journal WHERE revision=1",
        "DELETE FROM trading_journal WHERE revision=2",
        "UPDATE trading_journal SET command_json='{}' WHERE revision=1",
    ],
)
async def test_risk_journal_corruption_refuses_recovery(demo_settings: Settings, sql: str) -> None:
    assert demo_settings.ledger is not None
    service = make_service(demo_settings)
    await service.start()
    await service.publish_frame(frame(1))
    await service.publish_frame(frame(2, "3350"))
    await service.close()
    with sqlite3.connect(demo_settings.ledger.database) as connection:
        connection.execute(sql)
    with pytest.raises(StorageError):
        await make_service(demo_settings).start()


async def test_real_http_circuit_breaker_and_session_boundary(demo_settings: Settings) -> None:
    settings = demo_settings.model_copy(
        update={"trading": TradingSettings(default_product_limit="2000000")}
    )
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        body = {
            "request_id": "http-open",
            "account_id": "A",
            "instrument_id": "SHFE.rb2610",
            "trading_day": "2026-09-09",
            "side": "LONG",
            "offset": "OPEN",
            "quantity": 1,
        }
        assert (await client.post("/api/orders", json=body)).status_code == 401
        assert (
            await client.get("/api/session", headers={"Origin": "https://other.example"})
        ).status_code == 403
        session = await client.get("/api/session")
        assert session.headers["cache-control"] == "no-store"
        headers = {"Authorization": "Bearer " + session.json()["token"]}
        assert (
            await client.post(
                "/api/orders", json=body, headers={**headers, "Origin": "https://other.example"}
            )
        ).status_code == 403
        assert (await client.get("/api/health/ready")).status_code == 503
        assert (await client.get("/api/risk")).json()[0]["floating_pnl"] is None
        prices = {"SHFE.rb2610": "3350", "DCE.i2701": "800", "CFFEX.IF2609": "4000"}
        response = await client.post(
            "/api/simulation/frames",
            headers=headers,
            json={"sequence": 1, "trading_day": "2026-09-09", "prices": prices},
        )
        assert response.status_code == 200
        assert (await client.get("/api/health/ready")).status_code == 200
        assert (await client.get("/api/market")).json()["source"] == "manual-demo"
        risk = (await client.get("/api/risk?account_id=A")).json()[0]
        assert risk["floating_pnl"] == "-3000.00" and risk["circuit_broken"]
        trades_before = (await client.get("/api/trades")).json()
        rejected = (await client.post("/api/orders", json=body, headers=headers)).json()
        assert rejected["status"] == "REJECTED" and rejected["reason"] == "CIRCUIT_BROKEN"
        assert (await client.get("/api/trades")).json() == trades_before
        assert (await client.get("/api/orders/A/http-open")).json() == rejected
        assert (await client.post("/api/orders", json=body, headers=headers)).json()["duplicate"]
        assert (
            await client.post("/api/orders", json={**body, "quantity": 2}, headers=headers)
        ).status_code == 409
        for change in ({"quantity": 1.0}, {"price": "100"}, {"trading_day": "20260909"}):
            assert (
                await client.post("/api/orders", json={**body, **change}, headers=headers)
            ).status_code == 422
        close = (
            await client.post(
                "/api/orders",
                headers=headers,
                json={**body, "request_id": "http-close", "offset": "CLOSE_TODAY"},
            )
        ).json()
        assert close["status"] == "FILLED" and close["price"] == "3350" and close["fee"] == "2.00"
        assert len((await client.get("/api/orders?account_id=A&limit=1&offset=1")).json()) == 1
        events = (await client.get("/api/risk-events?account_id=A")).json()
        assert sum(item["kind"] == "CIRCUIT_BREAK" for item in events) == 1
        assert (await client.get("/api/risk?account_id=missing")).status_code == 404
    # New session token after restart, same breaker and accepted/rejected order results.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.post("/api/orders", json=body, headers=headers)).status_code == 401
        assert (await client.get("/api/risk?account_id=A")).json()[0]["circuit_broken"]
        assert (await client.get("/api/health/ready")).status_code == 503

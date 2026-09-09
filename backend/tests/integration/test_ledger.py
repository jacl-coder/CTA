"""真实 SQLite 事务、恢复、背压及只读 API；不作为风控验收。"""

import asyncio
import sqlite3
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from cta_risk.application.contracts import (
    ConfigurationMismatch,
    LedgerBusy,
    LedgerUnavailable,
    StorageError,
)
from cta_risk.application.ledger import LedgerService
from cta_risk.bootstrap import create_app
from cta_risk.config.models import Settings
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import AccountBook, Fill, Offset, Side
from cta_risk.storage.sqlite import SQLiteLedgerStore

pytestmark = [pytest.mark.asyncio, pytest.mark.requirement("POS-01", "POS-02", "POS-03", "POS-04")]


def service_for(settings: Settings, store: SQLiteLedgerStore | None = None) -> LedgerService:
    assert settings.ledger is not None
    return LedgerService(
        settings.ledger.definition(), store or SQLiteLedgerStore(settings.ledger.database)
    )


def next_fill(service: LedgerService, sequence: int = 3, **changes: object) -> Fill:
    fill = Fill(
        f"A-{sequence}",
        "A",
        "SHFE.rb2610",
        service.definition.trading_day,
        sequence,
        Side.LONG,
        Offset.OPEN,
        1,
        Decimal("3500"),
    )
    return replace(fill, **changes)


def rows(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in (
                "accounts",
                "account_books",
                "trades",
                "position_lots",
                "close_allocations",
            )
        }


async def test_seed_restart_and_duplicate_are_exactly_once(demo_settings: Settings) -> None:
    service = service_for(demo_settings)
    await service.start()
    try:
        a = service.account("A")
        assert (a.book.realized_pnl, a.book.fees, a.revision) == (Decimal("100"), Decimal("8"), 2)
        assert [item.quantity for item in service.positions()] == [2, 2, 1]
        fill = next_fill(service, offset=Offset.CLOSE_TODAY, price=Decimal("3520"))
        first = await service.record_fill(fill, "test-execution")
        duplicate = await service.record_fill(fill, "retry")
        assert not first.duplicate and duplicate.duplicate
        assert first.revision == duplicate.revision == 3
        assert service.account("A").book.fees == Decimal("10")
        assert service.account("A").book.realized_pnl == Decimal("300")
        assert service.positions("A")[0].quantity == 1
        before = service.accounts(), service.trades()
        with pytest.raises(AccountingError):
            await service.record_fill(replace(fill, price=Decimal("3530")), "test-execution")
        assert (service.accounts(), service.trades()) == before
        assert service.available
    finally:
        await service.close()
    for _ in range(2):
        service = service_for(demo_settings)
        await service.start()
        try:
            assert (service.accounts(), service.trades()) == before
            assert (await service.record_fill(fill, "retry-after-restart")).duplicate
        finally:
            await service.close()


async def test_concurrent_writes_do_not_lose_updates(demo_settings: Settings) -> None:
    service = service_for(demo_settings)
    await service.start()
    try:
        results = await asyncio.gather(
            *(
                service.record_fill(next_fill(service, sequence), "test-execution")
                for sequence in range(3, 23)
            )
        )
        assert [result.revision for result in results] == list(range(3, 23))
        assert service.positions("A")[0].quantity == 22
        assert service.account("A").book.fees == Decimal("48")
        expected = service.accounts()
    finally:
        await service.close()
    restored = service_for(demo_settings)
    await restored.start()
    try:
        assert restored.accounts() == expected
    finally:
        await restored.close()


class PausedStore(SQLiteLedgerStore):
    def __init__(self, path: Path):
        super().__init__(path)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.pause = False

    def _replace_positions(self, run_id: str, book: AccountBook) -> None:
        super()._replace_positions(run_id, book)
        if self.pause:
            self.entered.set()
            if not self.release.wait(5):
                raise sqlite3.OperationalError("test release timeout")


async def wait_until(predicate) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_cancellation_backpressure_and_shutdown_drain(demo_settings: Settings) -> None:
    assert demo_settings.ledger is not None
    config = demo_settings.ledger
    store = PausedStore(config.database)
    service = LedgerService(config.definition(), store, queue_capacity=1)
    await service.start()
    store.pause = True
    initial = rows(config.database)
    first_fill = next_fill(service)
    first = asyncio.create_task(service.record_fill(first_fill, "test-execution"))
    second = None
    closing = None
    try:
        await wait_until(store.entered.is_set)
        # SQL 已改写但尚未提交时，数据库读者与应用快照仍只能看到旧值。
        assert rows(config.database) == initial
        assert service.account("A").revision == 2
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.record_fill(next_fill(service, 4), "test-execution"))
        await wait_until(lambda: service._queue.full())
        with pytest.raises(LedgerBusy):
            await service.record_fill(next_fill(service, 5), "test-execution")
        closing = asyncio.create_task(service.close())
        await wait_until(lambda: not service.available)
        with pytest.raises(LedgerUnavailable):
            await service.record_fill(next_fill(service, 6), "test-execution")
        store.release.set()
        assert (await second).revision == 4
        await closing
    finally:
        store.release.set()
        await asyncio.gather(
            *(task for task in (first, second, closing) if task), return_exceptions=True
        )
        await service.close()
    restored = service_for(demo_settings)
    await restored.start()
    try:
        assert restored.account("A").revision == 4
        assert len(restored.trades("A")) == 4
        assert (await restored.record_fill(first_fill, "retry")).duplicate
        assert restored.account("A").book.fees == Decimal("12")
    finally:
        await restored.close()


class FailingStore(SQLiteLedgerStore):
    fail = False

    def _insert_allocations(self, run_id: str, book: AccountBook, fill_id: str | None) -> None:
        super()._insert_allocations(run_id, book, fill_id)
        if self.fail:
            raise sqlite3.OperationalError("injected failure after all SQL changes")


async def test_failed_transaction_rolls_back_all_tables_and_allows_recovery(
    demo_settings: Settings,
) -> None:
    assert demo_settings.ledger is not None
    path = demo_settings.ledger.database
    store = FailingStore(path)
    service = service_for(demo_settings, store)
    await service.start()
    before = rows(path)
    fill = next_fill(service, offset=Offset.CLOSE_TODAY, price=Decimal("3520"))
    try:
        store.fail = True
        with pytest.raises(LedgerUnavailable):
            await service.record_fill(fill, "test-execution")
        assert not service.available
        assert rows(path) == before
        with pytest.raises(LedgerUnavailable):
            service.accounts()
    finally:
        await service.close()
    restored = service_for(demo_settings)
    await restored.start()
    try:
        assert restored.account("A").revision == 2
        assert (await restored.record_fill(fill, "retry")).revision == 3
        assert restored.account("A").book.realized_pnl == Decimal("300")
    finally:
        await restored.close()


async def test_failed_initialization_does_not_leave_partial_seed(demo_settings: Settings) -> None:
    assert demo_settings.ledger is not None
    path = demo_settings.ledger.database
    store = FailingStore(path)
    store.fail = True
    with pytest.raises(StorageError):
        await service_for(demo_settings, store).start()
    assert all(not records for records in rows(path).values())
    restored = service_for(demo_settings)
    await restored.start()
    try:
        assert len(restored.trades()) == 4
    finally:
        await restored.close()


async def test_configuration_mismatch_and_second_writer_are_rejected(
    demo_settings: Settings,
) -> None:
    assert demo_settings.ledger is not None
    service = service_for(demo_settings)
    await service.start()
    try:
        with pytest.raises(StorageError, match="占用"):
            await service_for(demo_settings).start()
        before = rows(demo_settings.ledger.database)
    finally:
        await service.close()
    definition = replace(service.definition, run_id="different-run")
    with pytest.raises(ConfigurationMismatch):
        await LedgerService(definition, SQLiteLedgerStore(demo_settings.ledger.database)).start()
    assert rows(demo_settings.ledger.database) == before
    restored = service_for(demo_settings)
    await restored.start()
    await restored.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE account_books SET fees='0' WHERE account_id='A'",
        "UPDATE position_lots SET quantity=1 WHERE account_id='A'",
        "UPDATE instruments SET multiplier=1 WHERE product='rb'",
        "UPDATE schema_migrations SET checksum='changed'",
        "DELETE FROM trades WHERE account_id='A' AND sequence=1",
    ],
)
async def test_corruption_is_not_silently_loaded(demo_settings: Settings, sql: str) -> None:
    assert demo_settings.ledger is not None
    service = service_for(demo_settings)
    await service.start()
    await service.close()
    with sqlite3.connect(demo_settings.ledger.database) as connection:
        connection.execute(sql)
    with pytest.raises((StorageError, AccountingError)):
        await service_for(demo_settings).start()


async def test_multi_account_aggregation_preserves_direction_and_independent_fees(
    demo_settings: Settings,
) -> None:
    service = service_for(demo_settings)
    await service.start()
    try:
        await service.record_fill(
            next_fill(service, 2, account_id="B", fill_id="B-rb-long", quantity=4), "test"
        )
        await service.record_fill(
            next_fill(
                service, 3, account_id="B", fill_id="B-rb-short", quantity=3, side=Side.SHORT
            ),
            "test",
        )
        summary = service.position_summary("SHFE.rb")
        assert [(item.side, item.quantity) for item in summary] == [(Side.LONG, 6), (Side.SHORT, 3)]
        assert service.account("A").book.fees == Decimal("8")
        assert service.account("B").book.fees == Decimal("20")
        assert len(service.trades(product_id="SHFE.rb")) == 4
    finally:
        await service.close()


async def test_read_only_api_with_real_lifespan(demo_settings: Settings) -> None:
    app = create_app(demo_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        accounts = (await client.get("/api/accounts")).json()
        assert [item["account_id"] for item in accounts] == ["A", "B", "C"]
        a = (await client.get("/api/accounts/A")).json()
        assert a["realized_pnl"] == "100.00" and a["fees"] == "8.00"
        assert isinstance(a["initial_capital"], str)
        positions = (
            await client.get("/api/positions", params={"account_id": "A", "product_id": "SHFE.rb"})
        ).json()
        assert len(positions) == 1 and positions[0]["quantity"] == 2
        summary = (await client.get("/api/positions/summary?product_id=SHFE.rb")).json()
        assert len(summary) == 1 and summary[0]["quantity"] == 2
        trades = (
            await client.get("/api/trades?account_id=A&product_id=SHFE.rb&limit=1&offset=1")
        ).json()
        assert len(trades) == 1 and trades[0]["fill_id"] == "A-close-2"
        assert trades[0]["fee"] == "2.00" and trades[0]["source"] == "bootstrap-demo"
        assert (await client.get("/api/positions?product_id=unknown")).json() == []
        for route in (
            "/api/accounts/missing",
            "/api/positions?account_id=missing",
            "/api/trades?account_id=missing",
        ):
            assert (await client.get(route)).status_code == 404
        assert (await client.get("/api/trades?limit=0")).status_code == 422
        assert (await client.post("/api/trades", json={})).status_code == 405
        status = (await client.get("/api/system")).json()
        assert status["stage"] == "ledger" and status["ledger_available"]
        assert not status["trading_available"]
        assert (await client.get("/api/health/ready")).status_code == 503
        await app.state.ledger.close()
        assert (await client.get("/api/accounts")).status_code == 503
    assert app.state.ledger is None


async def test_no_ledger_config_is_explicitly_unavailable() -> None:
    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/api/accounts")).status_code == 503

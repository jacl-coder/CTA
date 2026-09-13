"""SET-01/SET-02：手工四帧、独立手算金额、真实 SQLite 日结事务与重放。"""

import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from cta_risk.application.contracts import LedgerUnavailable, StorageError
from cta_risk.application.ledger import LedgerService
from cta_risk.bootstrap import create_app
from cta_risk.config.models import Settings
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import AccountBook, Offset, Side
from cta_risk.domain.settlement import DayCommand
from cta_risk.domain.trading import CommandConflict, Order, PriceFrame
from cta_risk.storage.sqlite import SQLiteLedgerStore

D = Decimal
DAY1 = date(2026, 9, 9)
DAY2 = date(2026, 9, 10)
RB = "SHFE.rb2610"
IRON = "DCE.i2701"
IF = "CFFEX.IF2609"
pytestmark = [pytest.mark.asyncio, pytest.mark.requirement("SET-01", "SET-02")]

MONEY_FIELDS = (
    "opening_balance",
    "realized_pnl",
    "holding_pnl",
    "fees",
    "net_pnl",
    "closing_balance",
    "margin",
    "available",
)
# 预期值全部独立手算，不调用清算/估值函数生成 oracle。
# D1 A: 平今 (3510-3500)*1*10=100；余仓 (3510-3500)*2*10=200；
#       费用 (3+1)*2=8；保证金 3510*2*10*0.10=7020。
# D1 B: (800-810)*2*100=-2000，费用 2*3=6，保证金 810*2*100*0.12=19440。
# D1 C: (4005-4000)*1*300=1500，费用 5，保证金 4005*300*0.12=144180。
DAY1_MONEY = {
    "A": "100000 100 200 8 292 100292 7020 93272",
    "B": "200000 0 -2000 6 -2006 197994 19440 178554",
    "C": "1000000 0 1500 5 1495 1001495 144180 857315",
}
# D2 A: 平昨 (3520-3510)*1*10=100；余仓 (3490-3510)*1*10=-200；
#       费用 2；保证金 3490*1*10*0.10=3490；期末 100292+100-200-2=100190。
# D2 B: (810-800)*2*100=2000；D2 C: (4010-4005)*300=1500；两者无新成交费。
DAY2_MONEY = {
    "A": "100292 100 -200 2 -102 100190 3490 96700",
    "B": "197994 0 2000 0 2000 199994 19200 180794",
    "C": "1001495 0 1500 0 1500 1002995 144360 858635",
}
TOTALS = {
    DAY1: "1300000 100 -300 19 -219 1299781 170640 1129141",
    DAY2: "1299781 100 3300 2 3398 1303179 167050 1136129",
}


@pytest.fixture
def settlement_settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[3]
    raw = yaml.safe_load(
        (root / "testdata/configs/settlement-demo.yaml").read_text(encoding="utf-8")
    )
    raw.pop("market")
    raw["settlement"]["auto_settle"] = False
    raw["ledger"]["database"] = tmp_path / "ledger.sqlite3"
    raw["settlement"]["report_dir"] = tmp_path / "reports"
    demo = Settings.model_validate(raw)
    assert demo.ledger is not None and demo.settlement is not None
    plan = demo.settlement.plan(demo.ledger)
    assert [(day.session.trading_day, day.session.close_sequence) for day in plan.days] == [
        (DAY1, 64),
        (DAY2, 96),
    ]
    # 仅缩短测试内的全局收盘序号，保留示例的账户、成交、价格和两日计划。
    for day, sequence in zip(raw["settlement"]["days"], (2, 4), strict=True):
        day["close_sequence"] = sequence
    return Settings.model_validate(raw)


def make_service(settings: Settings, store: SQLiteLedgerStore | None = None) -> LedgerService:
    assert settings.ledger is not None
    assert settings.trading is not None and settings.settlement is not None
    return LedgerService(
        settings.ledger.definition(),
        store if store is not None else SQLiteLedgerStore(settings.ledger.database),
        policy=settings.trading.policy(),
        settlement_plan=settings.settlement.plan(settings.ledger),
        clock=lambda: 100.0,  # 手工行情不因测试机调度速度而过期。
    )


def prices(rb: str, iron: str, index: str) -> tuple[tuple[str, Decimal], ...]:
    return tuple(sorted(((RB, D(rb)), (IRON, D(iron)), (IF, D(index)))))


FRAMES = (
    PriceFrame(1, DAY1, prices("3350", "800", "4000")),
    PriceFrame(2, DAY1, prices("3500", "800", "4000")),
    PriceFrame(3, DAY2, prices("3520", "805", "4010")),
    PriceFrame(4, DAY2, prices("3480", "795", "4020")),
)
SETTLE1 = DayCommand("settle", DAY1, prices("3510", "810", "4005"))
SETTLE2 = DayCommand("settle", DAY2, prices("3490", "800", "4010"))


def order(key: str, day: date, offset: Offset = Offset.OPEN) -> Order:
    return Order(key, "A", RB, day, Side.LONG, offset, 1)


def rows(path: Path) -> dict[str, list[tuple]]:
    """包含账户修订号、持仓、成交、日志检查点和报告的全部业务表快照。"""
    with sqlite3.connect(path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {
            name: connection.execute(
                'SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY rowid'
            ).fetchall()
            for (name,) in tables
        }


def assert_report(service: LedgerService, day: date, expected: dict[str, str]) -> None:
    report = json.loads(service.trading_state().reports[-1])
    command = SETTLE1 if day == DAY1 else SETTLE2
    assert report["trading_day"] == str(day)
    assert report["close_sequence"] == (2 if day == DAY1 else 4)
    assert report["run_id"] == service.definition.run_id
    assert {key: D(value) for key, value in report["prices"].items()} == dict(command.prices)
    entries = {entry["account_id"]: entry for entry in report["accounts"]}
    assert len(report["accounts"]) == 3 and set(entries) == {"A", "B", "C"}
    for account_id, amounts in expected.items():
        values = tuple(map(D, amounts.split()))
        settlement = service.account(account_id).book.settlement
        assert settlement is not None and settlement.trading_day == day
        assert tuple(getattr(settlement, field) for field in MONEY_FIELDS) == values
        entry = entries[account_id]
        assert tuple(D(entry["settlement"][field]) for field in MONEY_FIELDS) == values
        valuation = entry["valuation"]
        assert tuple(D(valuation[field]) for field in ("equity", "margin", "available")) == (
            values[5],
            values[6],
            values[7],
        )
    assert tuple(D(report["totals"][field]) for field in MONEY_FIELDS) == tuple(
        map(D, TOTALS[day].split())
    )
    # 空头敞口只在净额中扣减，绝对敞口仍为相加。
    assert tuple(D(report["totals"][field]) for field in ("gross_exposure", "net_exposure")) == (
        (D("1433700"), D("1109700")) if day == DAY1 else (D("1397900"), D("1077900"))
    )


async def close_first_day(service: LedgerService) -> None:
    await service.publish_frame(FRAMES[0])
    await service.day_command(DayCommand("close", DAY1))
    await service.publish_frame(FRAMES[1])


async def restart(settings: Settings, service: LedgerService) -> LedgerService:
    assert settings.ledger is not None
    expected = service.trading_state(), service.accounts(), service.trades(), service.positions()
    before = rows(settings.ledger.database)
    await service.close()
    restored = make_service(settings)
    try:
        await restored.start()
        assert (
            restored.trading_state(),
            restored.accounts(),
            restored.trades(),
            restored.positions(),
        ) == expected
        assert rows(settings.ledger.database) == before
        assert not restored.trading_available  # 重放旧帧不能恢复行情新鲜度。
    except BaseException:
        await restored.close()
        raise
    return restored


async def test_two_day_money_rollover_close_yesterday_and_restart(
    settlement_settings: Settings,
) -> None:
    service = make_service(settlement_settings)
    await service.start()
    try:
        await close_first_day(service)
        assert next(
            risk for risk in service.trading_state().risks if risk.account_id == "A"
        ).circuit_broken
        assert not (await service.day_command(SETTLE1)).duplicate
        assert_report(service, DAY1, DAY1_MONEY)
        first_report = service.trading_state().reports[0]
        assert json.loads(first_report)["accounts"][0]["risk"]["circuit_broken"]
        assert [lot.basis for item in service.accounts() for lot in item.book.lots] == [
            D("3500"),
            D("800"),
            D("4000"),
        ]
        service = await restart(settlement_settings, service)
        assert (await service.day_command(SETTLE1)).duplicate
        opened = await service.day_command(DayCommand("open_day", DAY2))
        assert opened.phase == "OPEN" and not opened.duplicate
        assert not service.trading_available
        for account_id, balance, basis in (
            ("A", "100292", "3510"),
            ("B", "197994", "810"),
            ("C", "1001495", "4005"),
        ):
            book = service.account(account_id).book
            assert book.trading_day == DAY2 and book.opening_balance == D(balance)
            assert book.realized_pnl == book.fees == D("0")
            assert book.settlement is None and book.close_allocations == ()
            assert all(lot.basis == D(basis) and lot.opened_day == DAY1 for lot in book.lots)
        assert [
            (p.account_id, p.today_quantity, p.yesterday_quantity) for p in service.positions()
        ] == [("A", 0, 2), ("B", 0, 2), ("C", 0, 1)]
        assert all(not risk.circuit_broken for risk in service.trading_state().risks)
        assert len(service.trades()) == 4
        service = await restart(settlement_settings, service)
        assert (
            await service.submit_order(order("before-new-frame", DAY2))
        ).reason == "MARKET_UNAVAILABLE"
        await service.publish_frame(FRAMES[2])
        assert service.trading_available
        assert (
            await service.submit_order(order("wrong-offset", DAY2, Offset.CLOSE_TODAY))
        ).reason == "INVALID_POSITION"
        close_order = order("close-yesterday", DAY2, Offset.CLOSE_YESTERDAY)
        filled = await service.submit_order(close_order)
        assert (filled.status, filled.price, filled.fee) == ("FILLED", D("3520"), D("2"))
        book = service.account("A").book
        assert (book.realized_pnl, book.fees) == (D("100"), D("2"))
        assert len(book.close_allocations) == 1
        allocation = book.close_allocations[0]
        assert (allocation.quantity, allocation.basis, allocation.close_price, allocation.pnl) == (
            1,
            D("3510"),
            D("3520"),
            D("100"),
        )
        assert service.positions("A")[0].yesterday_quantity == 1
        service = await restart(settlement_settings, service)
        assert (await service.submit_order(close_order)) == replace(filled, duplicate=True)
        await service.publish_frame(FRAMES[3])
        await service.day_command(DayCommand("close", DAY2))
        await service.day_command(SETTLE2)
        assert_report(service, DAY2, DAY2_MONEY)
        assert service.trading_state().reports[0] == first_report
        assert [frame.sequence for frame in service.trading_state().frames] == [1, 2, 3, 4]
        assert len(service.trades()) == 5
        assert all(
            entry["risk_event_count"] == 0
            for entry in json.loads(service.trading_state().reports[1])["accounts"]
        )
        service = await restart(settlement_settings, service)
        before = service.trading_state()
        assert (await service.day_command(SETTLE1)).duplicate
        assert (await service.day_command(SETTLE2)).duplicate
        assert service.trading_state() == before
    finally:
        await service.close()


async def test_settlement_requires_closing_and_complete_close_frame(
    settlement_settings: Settings,
) -> None:
    assert settlement_settings.ledger is not None
    service = make_service(settlement_settings)
    await service.start()
    try:
        await service.publish_frame(FRAMES[0])
        await service.day_command(DayCommand("close", DAY1))
        before = service.trading_state(), rows(settlement_settings.ledger.database)
        assert (await service.day_command(DayCommand("close", DAY1))).duplicate
        with pytest.raises(AccountingError):
            await service.day_command(SETTLE1)
        with pytest.raises(AccountingError):
            await service.publish_frame(replace(FRAMES[1], prices=FRAMES[1].prices[:-1]))
        with pytest.raises(AccountingError):
            await service.day_command(SETTLE1)
        with pytest.raises(AccountingError):
            await service.publish_frame(FRAMES[2])
        assert (service.trading_state(), rows(settlement_settings.ledger.database)) == before
        assert service.available
        await service.publish_frame(FRAMES[1])
        await service.day_command(SETTLE1)
        assert_report(service, DAY1, DAY1_MONEY)
    finally:
        await service.close()


async def test_day_ordering_and_settled_rejects_trades(settlement_settings: Settings) -> None:
    service = make_service(settlement_settings)
    await service.start()
    try:
        for phase in ("OPEN", "CLOSING"):
            assert service.trading_state().phase == phase
            before = service.trading_state()
            with pytest.raises(AccountingError):
                await service.day_command(DayCommand("open_day", DAY2))
            assert service.trading_state() == before
            if phase == "OPEN":
                await close_first_day(service)
        await service.day_command(SETTLE1)
        before = service.trading_state()
        with pytest.raises(AccountingError):
            await service.day_command(DayCommand("open_day", date(2026, 9, 11)))
        with pytest.raises(AccountingError):
            await service.publish_frame(FRAMES[2])
        assert service.trading_state() == before
        assert not service.trading_available
        books, trades = service.accounts(), service.trades()
        for offset in (Offset.OPEN, Offset.CLOSE_TODAY, Offset.CLOSE_YESTERDAY):
            result = await service.submit_order(order(f"settled-{offset}", DAY1, offset))
            assert (result.status, result.reason, result.fill_id) == (
                "REJECTED",
                "TRADING_DAY_CLOSED",
                None,
            )
        with pytest.raises(LedgerUnavailable):
            await service.record_fill(replace(trades[0].fill, fill_id="bypass-settlement"), "test")
        assert (service.accounts(), service.trades()) == (books, trades)
        await service.day_command(DayCommand("open_day", DAY2))
        before = service.trading_state()
        assert (await service.day_command(DayCommand("open_day", DAY2))).duplicate
        with pytest.raises(AccountingError):
            await service.day_command(DayCommand("open_day", DAY1))
        assert service.trading_state() == before
    finally:
        await service.close()


async def test_http_duplicate_settlement_and_changed_prices_conflict(
    settlement_settings: Settings,
) -> None:
    assert settlement_settings.ledger is not None
    app = create_app(settlement_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        service = app.state.ledger
        session = await client.get("/api/session")
        assert session.status_code == 200
        client.headers["Authorization"] = "Bearer " + session.json()["token"]
        body = {"trading_day": str(DAY1), "prices": {k: str(v) for k, v in SETTLE1.prices}}
        await service.publish_frame(FRAMES[0])
        await service.publish_frame(FRAMES[1])
        before = service.trading_state(), rows(settlement_settings.ledger.database)
        # 即使收盘行情完整，OPEN 状态也不能直接日结。
        assert (await client.post("/api/settlement/settle", json=body)).status_code == 422
        assert (service.trading_state(), rows(settlement_settings.ledger.database)) == before
        assert (
            await client.post("/api/settlement/close", json={"trading_day": str(DAY1)})
        ).status_code == 200
        response = await client.post("/api/settlement/settle", json=body)
        assert response.status_code == 200
        assert response.json() == {"trading_day": str(DAY1), "phase": "SETTLED", "duplicate": False}
        before = service.trading_state(), rows(settlement_settings.ledger.database)
        duplicate = await client.post("/api/settlement/settle", json=body)
        assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True
        for changed in (
            {**body["prices"], RB: "3511"},
            {key: value for key, value in body["prices"].items() if key != IF},
        ):
            conflict = await client.post("/api/settlement/settle", json={**body, "prices": changed})
            assert conflict.status_code == 409
        assert (service.trading_state(), rows(settlement_settings.ledger.database)) == before
        await service.day_command(DayCommand("open_day", DAY2))
        before = service.trading_state(), rows(settlement_settings.ledger.database)
        assert (await client.post("/api/settlement/settle", json=body)).json()["duplicate"] is True
        with pytest.raises(CommandConflict):
            await service.day_command(replace(SETTLE1, prices=prices("3511", "810", "4005")))
        assert (service.trading_state(), rows(settlement_settings.ledger.database)) == before


class FailOnSecondAccountStore(SQLiteLedgerStore):
    def __init__(self, path: Path):
        super().__init__(path)
        self.fail = False
        self.replaced_accounts: list[str] = []
        self.failed_in_transaction = False

    def _replace_positions(self, run_id: str, book: AccountBook) -> None:
        super()._replace_positions(run_id, book)
        if self.fail:
            self.replaced_accounts.append(book.account.account_id)
            if len(self.replaced_accounts) == 2:
                # 此处仍在日结事务内部；在 save_day_transition 的 super 后抛错已太迟。
                self.failed_in_transaction = self._db().in_transaction
                raise sqlite3.OperationalError("injected before commit on second account")


@pytest.mark.parametrize("action", ["settle", "open_day"])
async def test_all_accounts_rollback_before_commit_and_retry_after_restart(
    settlement_settings: Settings,
    action: str,
) -> None:
    assert settlement_settings.ledger is not None
    path = settlement_settings.ledger.database
    store = FailOnSecondAccountStore(path)
    service = make_service(settlement_settings, store)
    await service.start()
    try:
        await close_first_day(service)
        if action == "open_day":
            await service.day_command(SETTLE1)
        command = SETTLE1 if action == "settle" else DayCommand("open_day", DAY2)
        state = service.trading_state()
        before = rows(path)
        assert {
            "account_books",
            "position_lots",
            "trading_runs",
            "trading_journal",
            "daily_reports",
        } <= before.keys()
        store.fail = True
        with pytest.raises(LedgerUnavailable):
            await service.day_command(command)
        assert store.replaced_accounts == ["A", "B"] and store.failed_in_transaction
        assert not service.available
        assert rows(path) == before
        assert service._trading is state  # 事务失败不能发布候选内存状态。
        with pytest.raises(LedgerUnavailable):
            service.accounts()
    finally:
        await service.close()
    restored = make_service(settlement_settings)
    await restored.start()
    try:
        assert restored.trading_state() == state
        assert rows(path) == before
        result = await restored.day_command(command)
        assert not result.duplicate and result.phase == (
            "SETTLED" if action == "settle" else "OPEN"
        )
        assert [item.revision for item in restored.accounts()] == [
            item.revision + 1 for item in state.ledger.accounts
        ]
        if action == "settle":
            assert_report(restored, DAY1, DAY1_MONEY)
        else:
            assert [item.book.opening_balance for item in restored.accounts()] == [
                D("100292"),
                D("197994"),
                D("1001495"),
            ]
            assert all(
                item.book.fees == item.book.realized_pnl == D("0") for item in restored.accounts()
            )
            assert [lot.basis for item in restored.accounts() for lot in item.book.lots] == [
                D("3510"),
                D("810"),
                D("4005"),
            ]
        after = restored.trading_state(), rows(path)
        assert (await restored.day_command(command)).duplicate
        assert (restored.trading_state(), rows(path)) == after
        restored = await restart(settlement_settings, restored)
    finally:
        await restored.close()


@pytest.mark.parametrize("opened", [False, True], ids=["settled", "rolled-over"])
@pytest.mark.parametrize("damage", ["checksum", "rehash", "delete"])
async def test_corrupted_report_refuses_restart(
    settlement_settings: Settings,
    opened: bool,
    damage: str,
) -> None:
    assert settlement_settings.ledger is not None
    path = settlement_settings.ledger.database
    service = make_service(settlement_settings)
    await service.start()
    try:
        await close_first_day(service)
        await service.day_command(SETTLE1)
        if opened:
            await service.day_command(DayCommand("open_day", DAY2))
    finally:
        await service.close()
    with sqlite3.connect(path) as connection:
        if damage == "delete":
            connection.execute("DELETE FROM daily_reports WHERE trading_day=?", (str(DAY1),))
        else:
            payload, checksum = connection.execute(
                "SELECT payload_json, payload_hash FROM daily_reports WHERE trading_day=?",
                (str(DAY1),),
            ).fetchone()
            report = json.loads(payload)
            report["totals"]["closing_balance"] = "0.00"
            payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
            if damage == "rehash":
                # 合法 JSON 且摘要自洽，仍须与权威日结日志重放逐字核对。
                checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            connection.execute(
                "UPDATE daily_reports SET payload_json=?, payload_hash=? WHERE trading_day=?",
                (payload, checksum, str(DAY1)),
            )
    corrupted = rows(path)
    restored = make_service(settlement_settings)
    try:
        with pytest.raises(StorageError):
            await restored.start()
        assert not restored.available
        with pytest.raises(LedgerUnavailable):
            restored.accounts()
        assert rows(path) == corrupted
    finally:
        await restored.close()


@pytest.mark.parametrize("damage", ["delete", "change"])
async def test_prior_day_allocations_remain_verified_after_rollover(
    settlement_settings: Settings, damage: str
) -> None:
    assert settlement_settings.ledger is not None
    service = make_service(settlement_settings)
    await service.start()
    await close_first_day(service)
    await service.day_command(SETTLE1)
    await service.day_command(DayCommand("open_day", DAY2))
    await service.close()
    with sqlite3.connect(settlement_settings.ledger.database) as connection:
        if damage == "delete":
            connection.execute("DELETE FROM close_allocations")
        else:
            connection.execute("UPDATE close_allocations SET pnl='999.00'")
    restored = make_service(settlement_settings)
    with pytest.raises(StorageError, match="历史平仓分摊"):
        await restored.start()
    assert not restored.available

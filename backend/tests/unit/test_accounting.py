"""按手算预期验证 POS-01/POS-02/POS-04 与 SET-01，非系统级验收。"""

import json
from dataclasses import replace
from datetime import date
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from pathlib import Path

import pytest

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import (
    Account,
    AccountBook,
    Exchange,
    Fill,
    Instrument,
    Offset,
    Side,
    new_book,
)
from cta_risk.domain.numbers import decimal_value, money
from cta_risk.position.book import apply_fill
from cta_risk.position.query import aggregate_exposure, query_positions
from cta_risk.settlement.calculator import settle_account, start_next_day
from cta_risk.valuation.calculator import value_account

D = Decimal
DAY1 = date(2026, 9, 9)
DAY2 = date(2026, 9, 10)
RB = Instrument("SHFE.rb2610", "rb", Exchange.SHFE, 10, D("1"), D("0.10"), D("2"))
IRON = Instrument("DCE.i2701", "i", Exchange.DCE, 100, D("0.5"), D("0.12"), D("3"))
INDEX = Instrument("CFFEX.IF2609", "IF", Exchange.CFFEX, 300, D("0.2"), D("0.12"), D("5"))


def fill(
    book: AccountBook,
    instrument: Instrument,
    sequence: int,
    quantity: int,
    price: str,
    offset: Offset = Offset.OPEN,
    side: Side = Side.LONG,
) -> Fill:
    return Fill(
        f"{book.trading_day}-{sequence}",
        book.account.account_id,
        instrument.instrument_id,
        book.trading_day,
        sequence,
        side,
        offset,
        quantity,
        D(price),
    )


def trade(
    book: AccountBook,
    instrument: Instrument,
    sequence: int,
    quantity: int,
    price: str,
    offset: Offset = Offset.OPEN,
    side: Side = Side.LONG,
) -> AccountBook:
    return apply_fill(
        book, fill(book, instrument, sequence, quantity, price, offset, side), instrument
    )


def first_day() -> dict[str, AccountBook]:
    a = new_book(Account("A", D("100000.00")), DAY1)
    a = trade(a, RB, 1, 3, "3500")
    a = trade(a, RB, 2, 1, "3510", Offset.CLOSE_TODAY)
    b = new_book(Account("B", D("200000.00")), DAY1)
    b = trade(b, IRON, 1, 2, "800", side=Side.SHORT)
    c = new_book(Account("C", D("1000000.00")), DAY1)
    c = trade(c, INDEX, 1, 1, "4000")
    return {"A": a, "B": b, "C": c}


def second_day(closed: dict[str, AccountBook]) -> dict[str, AccountBook]:
    books = {key: start_next_day(book, DAY2) for key, book in closed.items()}
    a = trade(books["A"], RB, 1, 1, "3510", Offset.CLOSE_YESTERDAY)
    a = trade(a, RB, 2, 2, "3500")
    a = trade(a, RB, 3, 1, "3530", Offset.CLOSE_TODAY)
    b = trade(books["B"], IRON, 1, 1, "795", Offset.CLOSE_YESTERDAY, Side.SHORT)
    c = trade(books["C"], INDEX, 1, 1, "4000", Offset.CLOSE_YESTERDAY)
    return {"A": a, "B": b, "C": c}


@pytest.mark.requirement("POS-01", "POS-04", "SET-01")
def test_two_day_results_match_independent_hand_calculation() -> None:
    expected_path = (
        Path(__file__).resolve().parents[3] / "testdata/expected/accounting-two-days.json"
    )
    expected = json.loads(expected_path.read_text())
    prices1 = {
        RB.instrument_id: D("3520"),
        IRON.instrument_id: D("790"),
        INDEX.instrument_id: D("4001"),
    }
    prices2 = {RB.instrument_id: D("3540"), IRON.instrument_id: D("780")}
    closed1 = {key: settle_account(book, prices1) for key, book in first_day().items()}
    closed2 = {key: settle_account(book, prices2) for key, book in second_day(closed1).items()}
    for trading_day, books in ((DAY1, closed1), (DAY2, closed2)):
        for account_id, book in books.items():
            result = book.settlement
            assert result is not None
            for field, value in expected[str(trading_day)][account_id].items():
                assert getattr(result, field) == D(value), (trading_day, account_id, field)
            assert sum((line.pnl for line in result.lines), D("0")) == result.holding_pnl
            assert result.opening_balance + result.net_pnl == result.closing_balance
            assert result.closing_balance - result.margin == result.available
    assert closed1["A"].lots[0].basis == D("3500")  # Historical day snapshot is unchanged.
    assert closed2["A"].lots[0].basis == D("3520")
    assert closed2["A"].lots[0].open_price == D("3500")
    assert closed2["C"].lots == ()


@pytest.mark.requirement("POS-01")
def test_fifo_partial_close_has_auditable_lot_allocations() -> None:
    book = new_book(Account("A", D("100000")), DAY1)
    book = trade(book, RB, 1, 2, "3500")
    book = trade(book, RB, 2, 2, "3510")
    closed = trade(book, RB, 3, 3, "3520", Offset.CLOSE_TODAY)
    assert [(item.quantity, item.basis, item.pnl) for item in closed.close_allocations] == [
        (2, D("3500"), D("400")),
        (1, D("3510"), D("100")),
    ]
    assert closed.lots[0].quantity == 1
    assert closed.lots[0].open_price == D("3510")
    assert [lot.quantity for lot in book.lots] == [2, 2]


@pytest.mark.requirement("POS-01", "POS-04")
def test_duplicate_fill_is_idempotent_but_conflicting_id_is_rejected() -> None:
    before = new_book(Account("A", D("100000")), DAY1)
    execution = fill(before, RB, 1, 1, "3500")
    after = apply_fill(before, execution, RB)
    assert apply_fill(after, execution, RB) is after
    with pytest.raises(AccountingError, match="冲突"):
        apply_fill(after, replace(execution, quantity=2), RB)
    with pytest.raises(AccountingError, match="账户"):
        apply_fill(after, replace(execution, account_id="B"), RB)
    assert after.fees == D("2.00")


@pytest.mark.requirement("POS-01")
@pytest.mark.parametrize(
    "offset,side,quantity",
    [
        (Offset.CLOSE_TODAY, Side.LONG, 2),
        (Offset.CLOSE_YESTERDAY, Side.LONG, 1),
        (Offset.CLOSE_TODAY, Side.SHORT, 1),
    ],
)
def test_invalid_close_cannot_change_positions(offset: Offset, side: Side, quantity: int) -> None:
    book = trade(new_book(Account("A", D("100000")), DAY1), RB, 1, 1, "3500")
    with pytest.raises(AccountingError, match="可平数量不足"):
        trade(book, RB, 2, quantity, "3510", offset, side)
    assert book.lots[0].quantity == 1 and book.fees == D("2")


@pytest.mark.requirement("POS-02", "POS-04")
def test_account_and_product_queries_need_no_market_prices() -> None:
    books = first_day()
    assert [
        (p.account_id, p.quantity, p.today_quantity)
        for p in query_positions(
            books.values(),
            product_id="SHFE.rb",
        )
    ] == [("A", 2, 2)]
    assert [p.instrument_id for p in query_positions(books.values(), account_id="B")] == [
        IRON.instrument_id
    ]
    closed = settle_account(books["A"], {RB.instrument_id: D("3520")})
    a = trade(start_next_day(closed, DAY2), RB, 1, 1, "3530")
    position = query_positions([a])[0]
    assert (position.quantity, position.today_quantity, position.yesterday_quantity) == (3, 1, 2)
    with pytest.raises(AccountingError, match="多个账本"):
        query_positions([a, a])


@pytest.mark.requirement("POS-04", "SET-01")
def test_long_short_and_accounts_are_not_netted_for_margin() -> None:
    a = trade(new_book(Account("A", D("100000")), DAY1), RB, 1, 1, "3500")
    b = trade(new_book(Account("B", D("200000")), DAY1), RB, 1, 1, "3500", side=Side.SHORT)
    quotes = {RB.instrument_id: D("3500")}
    av, bv = value_account(a, quotes), value_account(b, quotes)
    exposure = aggregate_exposure((*av.positions, *bv.positions))[0]
    assert exposure.gross_notional == D("70000")
    assert exposure.net_notional == D("0")
    assert av.margin == bv.margin == D("3500")
    assert av.equity == D("99998") and bv.equity == D("199998")


@pytest.mark.requirement("SET-01")
def test_settlement_repeat_and_rollover_are_explicit() -> None:
    book = first_day()["A"]
    with pytest.raises(AccountingError, match="尚未清算"):
        start_next_day(book, DAY2)
    with pytest.raises(AccountingError, match="缺少结算价"):
        settle_account(book, {})
    closed = settle_account(book, {RB.instrument_id: D("3520")})
    assert settle_account(closed, {RB.instrument_id: D("3520.00")}) is closed
    with pytest.raises(AccountingError, match="拒绝覆盖"):
        settle_account(closed, {RB.instrument_id: D("3521")})
    with pytest.raises(AccountingError, match="不能追加"):
        trade(closed, RB, 3, 1, "3520")
    with pytest.raises(AccountingError, match="晚于"):
        start_next_day(closed, DAY1)
    next_day = start_next_day(closed, DAY2)
    valuation = value_account(next_day, {RB.instrument_id: D("3520")})
    assert valuation.floating_pnl == D("0")
    assert valuation.equity == D("100492")
    assert next_day.fees == next_day.realized_pnl == D("0")


@pytest.mark.requirement("SET-01")
def test_decimal_context_cannot_change_accounting_results() -> None:
    with localcontext() as context:
        context.prec = 3
        context.rounding = ROUND_DOWN
        context.traps[Inexact] = True
        book = first_day()["A"]
        closed = settle_account(book, {RB.instrument_id: D("3520")})
        assert closed.settlement is not None
        assert closed.settlement.closing_balance == D("100492.00")
        assert money(D("1.005")) == D("1.01")
        assert money(D("-1.005")) == D("-1.01")
        assert context.prec == 3


@pytest.mark.requirement("SET-01")
def test_group_margin_does_not_change_when_position_is_split_into_lots() -> None:
    small = Instrument("TEST", "TEST", Exchange.SHFE, 1, D("0.01"), D("0.1"), D("0"))
    empty = new_book(Account("A", D("100")), DAY1)
    one = trade(empty, small, 1, 2, "0.05")
    split = trade(trade(empty, small, 1, 1, "0.05"), small, 2, 1, "0.05")
    quotes = {small.instrument_id: D("0.05")}
    assert value_account(one, quotes).margin == value_account(split, quotes).margin == D("0.01")


@pytest.mark.requirement("SET-01")
def test_settlement_price_is_not_a_trade_and_missing_marks_are_not_zero() -> None:
    book = first_day()["A"]
    with pytest.raises(AccountingError, match="缺少持仓合约价格"):
        value_account(book, {})
    with pytest.raises(AccountingError, match="最小变动价位"):
        trade(book, RB, 3, 1, "3500.5")
    result = settle_account(book, {RB.instrument_id: D("3520.05")}).settlement
    assert result is not None and result.holding_pnl == D("401.00")


@pytest.mark.requirement("POS-01", "SET-01")
def test_changed_contract_rules_and_out_of_order_fills_are_rejected() -> None:
    book = first_day()["A"]
    with pytest.raises(AccountingError, match="不能改变"):
        trade(book, replace(RB, multiplier=100), 3, 1, "3500")
    with pytest.raises(AccountingError, match="单调递增"):
        apply_fill(book, replace(fill(book, RB, 1, 1, "3500"), fill_id="new-id"), RB)
    with pytest.raises(AccountingError, match="交易日"):
        apply_fill(book, replace(fill(book, RB, 3, 1, "3500"), trading_day=DAY2), RB)


@pytest.mark.parametrize(
    "value", [1.1, 1, True, "NaN", "Infinity", "-Infinity", "1e100", "1e-9", "bad"]
)
def test_financial_inputs_reject_lossy_or_nonfinite_values(value: object) -> None:
    with pytest.raises(AccountingError):
        decimal_value(value)


@pytest.mark.parametrize("quantity", [0, -1, True, 1_000_001])
def test_quantity_is_positive_bounded_integer(quantity: int) -> None:
    book = new_book(Account("A", D("100000")), DAY1)
    with pytest.raises(AccountingError):
        fill(book, RB, 1, quantity, "3500")

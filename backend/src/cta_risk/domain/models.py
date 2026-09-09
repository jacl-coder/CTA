"""POS/SET 共用的不可变领域记录；不包含 HTTP、数据库或风控放行。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.numbers import ZERO, decimal_value, financial_context, money, positive_quantity


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AccountingError("标识必须是非空且无首尾空白的字符串")


class Exchange(StrEnum):
    SHFE = "SHFE"
    DCE = "DCE"
    CFFEX = "CFFEX"


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


class Offset(StrEnum):
    OPEN = "OPEN"
    CLOSE_TODAY = "CLOSE_TODAY"
    CLOSE_YESTERDAY = "CLOSE_YESTERDAY"


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    product: str
    exchange: Exchange
    multiplier: int
    tick_size: Decimal
    margin_rate: Decimal
    fee_per_lot: Decimal

    def __post_init__(self) -> None:
        identifier(self.instrument_id)
        identifier(self.product)
        if not isinstance(self.exchange, Exchange):
            raise AccountingError("交易所必须是 Exchange")
        positive_quantity(self.multiplier, "合约乘数")
        for name in ("tick_size", "margin_rate", "fee_per_lot"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise AccountingError(f"{name}必须是 Decimal")
            decimal_value(value, name)
        if self.tick_size <= 0 or not 0 < self.margin_rate <= 1 or self.fee_per_lot < 0:
            raise AccountingError("价位、保证金率或手续费非法")

    @property
    def product_id(self) -> str:
        return f"{self.exchange.value}.{self.product}"

    def validate_price(self, price: Decimal, *, execution: bool = False) -> None:
        if not isinstance(price, Decimal) or decimal_value(price, "价格") <= 0:
            raise AccountingError("价格必须是正 Decimal")
        if execution:
            with financial_context():
                if price % self.tick_size != 0:
                    raise AccountingError("成交价格不符合最小变动价位")


@dataclass(frozen=True)
class Account:
    account_id: str
    initial_capital: Decimal

    def __post_init__(self) -> None:
        identifier(self.account_id)
        if not isinstance(self.initial_capital, Decimal):
            raise AccountingError("初始资金必须是 Decimal")
        if (
            decimal_value(self.initial_capital) <= 0
            or money(self.initial_capital) != self.initial_capital
        ):
            raise AccountingError("初始资金必须为正且精确到分")


@dataclass(frozen=True)
class Fill:
    fill_id: str
    account_id: str
    instrument_id: str
    trading_day: date
    sequence: int
    side: Side
    offset: Offset
    quantity: int
    price: Decimal

    def __post_init__(self) -> None:
        for value in (self.fill_id, self.account_id, self.instrument_id):
            identifier(value)
        if type(self.trading_day) is not date:
            raise AccountingError("必须提供明确的交易日")
        if type(self.sequence) is not int or self.sequence < 1:
            raise AccountingError("成交顺序必须是正整数")
        if not isinstance(self.side, Side) or not isinstance(self.offset, Offset):
            raise AccountingError("持仓方向和开平标识非法")
        positive_quantity(self.quantity)
        if not isinstance(self.price, Decimal) or decimal_value(self.price, "成交价") <= 0:
            raise AccountingError("成交价必须是正 Decimal")


@dataclass(frozen=True)
class PositionLot:
    lot_id: str
    instrument_id: str
    side: Side
    opened_day: date
    opened_sequence: int
    quantity: int
    open_price: Decimal
    basis: Decimal

    def __post_init__(self) -> None:
        identifier(self.lot_id)
        identifier(self.instrument_id)
        positive_quantity(self.quantity)
        if not isinstance(self.side, Side) or type(self.opened_day) is not date:
            raise AccountingError("持仓方向或开仓交易日非法")
        if type(self.opened_sequence) is not int or self.opened_sequence < 1:
            raise AccountingError("开仓顺序非法")
        for value in (self.open_price, self.basis):
            if not isinstance(value, Decimal) or decimal_value(value) <= 0:
                raise AccountingError("持仓价格必须为正 Decimal")


@dataclass(frozen=True)
class CloseAllocation:
    fill_id: str
    lot_id: str
    quantity: int
    basis: Decimal
    close_price: Decimal
    pnl: Decimal


@dataclass(frozen=True)
class PositionSummary:
    account_id: str
    instrument_id: str
    product_id: str
    side: Side
    quantity: int
    today_quantity: int
    yesterday_quantity: int


@dataclass(frozen=True)
class PositionValue:
    account_id: str
    instrument_id: str
    product_id: str
    side: Side
    quantity: int
    notional: Decimal
    margin: Decimal
    floating_pnl: Decimal


@dataclass(frozen=True)
class Valuation:
    positions: tuple[PositionValue, ...]
    floating_pnl: Decimal
    equity: Decimal
    margin: Decimal
    available: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal


@dataclass(frozen=True)
class SettlementLine:
    lot_id: str
    instrument_id: str
    side: Side
    quantity: int
    basis: Decimal
    settlement_price: Decimal
    pnl: Decimal


@dataclass(frozen=True)
class DaySettlement:
    account_id: str
    trading_day: date
    prices: tuple[tuple[str, Decimal], ...]
    lines: tuple[SettlementLine, ...]
    opening_balance: Decimal
    realized_pnl: Decimal
    holding_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    closing_balance: Decimal
    margin: Decimal
    available: Decimal


@dataclass(frozen=True)
class AccountBook:
    account: Account
    trading_day: date
    opening_balance: Decimal
    instruments: tuple[Instrument, ...] = ()
    lots: tuple[PositionLot, ...] = ()
    fills: tuple[Fill, ...] = ()
    close_allocations: tuple[CloseAllocation, ...] = ()
    realized_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    settlement: DaySettlement | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.account, Account) or type(self.trading_day) is not date:
            raise AccountingError("账本账户或交易日非法")
        for value in (self.opening_balance, self.realized_pnl, self.fees):
            if not isinstance(value, Decimal) or not value.is_finite() or money(value) != value:
                raise AccountingError("账本金额必须是精确到分的有限 Decimal")
        if self.fees < 0:
            raise AccountingError("手续费不能为负")
        for items in (self.instruments, self.lots, self.fills, self.close_allocations):
            if type(items) is not tuple:
                raise AccountingError("账本记录必须是不可变元组")
        if len({item.instrument_id for item in self.instruments}) != len(self.instruments):
            raise AccountingError("账本包含重复合约规则")
        if len({lot.lot_id for lot in self.lots}) != len(self.lots):
            raise AccountingError("账本包含重复持仓批次")
        for lot in self.lots:
            if lot.opened_day > self.trading_day:
                raise AccountingError("持仓开仓日不能晚于账本交易日")
            instrument = self.instrument(lot.instrument_id)
            instrument.validate_price(lot.open_price, execution=True)
            instrument.validate_price(lot.basis)
            if lot.opened_day == self.trading_day and lot.basis != lot.open_price:
                raise AccountingError("今仓基准必须等于开仓价")

    def instrument(self, instrument_id: str) -> Instrument:
        for instrument in self.instruments:
            if instrument.instrument_id == instrument_id:
                return instrument
        raise AccountingError(f"缺少合约规则：{instrument_id}")


def new_book(account: Account, trading_day: date) -> AccountBook:
    if type(trading_day) is not date:
        raise AccountingError("必须提供明确的交易日")
    return AccountBook(account, trading_day, account.initial_capital)

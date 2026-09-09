"""账本配置边界：转换为不依赖 Pydantic 的应用/领域契约。"""

from datetime import date
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cta_risk.application.contracts import RecordedFill, RunDefinition
from cta_risk.application.ledger import seed_ledger
from cta_risk.domain.models import Account, Exchange, Fill, Instrument, Offset, Side, identifier
from cta_risk.domain.numbers import decimal_value


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AccountConfig(LedgerModel):
    account_id: str
    initial_capital: str

    def to_domain(self) -> Account:
        return Account(self.account_id, decimal_value(self.initial_capital))


class InstrumentConfig(LedgerModel):
    instrument_id: str
    product: str
    exchange: Literal["SHFE", "DCE", "CFFEX"]
    multiplier: int
    tick_size: str
    margin_rate: str
    fee_per_lot: str

    def to_domain(self) -> Instrument:
        return Instrument(
            self.instrument_id,
            self.product,
            Exchange(self.exchange),
            self.multiplier,
            decimal_value(self.tick_size),
            decimal_value(self.margin_rate),
            decimal_value(self.fee_per_lot),
        )


class InitialFillConfig(LedgerModel):
    fill_id: str
    account_id: str
    instrument_id: str
    sequence: int
    side: Literal["LONG", "SHORT"]
    offset: Literal["OPEN", "CLOSE_TODAY", "CLOSE_YESTERDAY"]
    quantity: int
    price: str
    source: str

    def to_record(self, trading_day: date) -> RecordedFill:
        identifier(self.source)
        return RecordedFill(
            Fill(
                self.fill_id,
                self.account_id,
                self.instrument_id,
                trading_day,
                self.sequence,
                Side(self.side),
                Offset(self.offset),
                self.quantity,
                decimal_value(self.price),
            ),
            self.source,
        )


class LedgerSettings(LedgerModel):
    database: Path
    run_id: str
    trading_day: str
    queue_capacity: int = Field(default=256, ge=1, le=4096)
    accounts: tuple[AccountConfig, ...]
    instruments: tuple[InstrumentConfig, ...]
    initial_fills: tuple[InitialFillConfig, ...] = ()

    @field_validator("accounts", "instruments", "initial_fills", mode="before")
    @classmethod
    def as_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    def definition(self) -> RunDefinition:
        trading_day = date.fromisoformat(self.trading_day)
        if self.trading_day != trading_day.isoformat():
            raise ValueError("交易日必须使用 YYYY-MM-DD 格式")
        return RunDefinition(
            self.run_id,
            trading_day,
            tuple(item.to_domain() for item in self.accounts),
            tuple(item.to_domain() for item in self.instruments),
            tuple(item.to_record(trading_day) for item in self.initial_fills),
        )

    @model_validator(mode="after")
    def valid_seed(self) -> Self:
        seed_ledger(self.definition())
        return self

"""可移植历史数据格式：冻结初始条件，按发生顺序保存行情、订单与日结。"""

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from cta_risk.application.trading import Command, OrderCommand
from cta_risk.config.ledger import (
    AccountConfig,
    InitialFillConfig,
    InstrumentConfig,
    LedgerSettings,
)
from cta_risk.config.settlement import SettlementDaySettings, SettlementSettings
from cta_risk.config.trading import TradingSettings
from cta_risk.domain.models import Offset, Side
from cta_risk.domain.numbers import decimal_value
from cta_risk.domain.settlement import DayCommand, SettlementPlan
from cta_risk.domain.trading import Order, PriceFrame

MAX_STEPS = 5000
MAX_BYTES = 8 * 1024 * 1024


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReplaySeed(InputModel):
    run_id: str
    trading_day: str
    accounts: list[AccountConfig] = Field(min_length=1, max_length=20)
    instruments: list[InstrumentConfig] = Field(min_length=1, max_length=30)
    initial_fills: list[InitialFillConfig] = Field(default_factory=list, max_length=1000)

    def ledger(self) -> LedgerSettings:
        # The path is required by config validation but never opened by the replay engine.
        return LedgerSettings(database=Path("unused-replay"), **self.model_dump())


class FrameStep(InputModel):
    kind: Literal["frame"]
    sequence: int
    trading_day: str
    prices: dict[str, str]
    source: str = "historical-replay"

    def command(self) -> PriceFrame:
        return PriceFrame(
            self.sequence,
            date.fromisoformat(self.trading_day),
            tuple(sorted((key, decimal_value(value)) for key, value in self.prices.items())),
            self.source,
        )


class ReplayOrder(InputModel):
    request_id: str
    account_id: str
    instrument_id: str
    trading_day: str
    side: Literal["LONG", "SHORT"]
    offset: Literal["OPEN", "CLOSE_TODAY", "CLOSE_YESTERDAY"]
    quantity: int

    def command(self) -> Order:
        return Order(
            self.request_id,
            self.account_id,
            self.instrument_id,
            date.fromisoformat(self.trading_day),
            Side(self.side),
            Offset(self.offset),
            self.quantity,
        )


class OrderStep(InputModel):
    kind: Literal["order"]
    order: ReplayOrder
    market_ready: bool
    processed_at_ms: int | None = None

    def command(self) -> OrderCommand:
        return OrderCommand(self.order.command(), self.market_ready, self.processed_at_ms)


class DayStep(InputModel):
    kind: Literal["close", "settle", "open_day"]
    trading_day: str
    prices: dict[str, str] = Field(default_factory=dict)

    def command(self) -> DayCommand:
        return DayCommand(
            self.kind,
            date.fromisoformat(self.trading_day),
            tuple(sorted((key, decimal_value(value)) for key, value in self.prices.items())),
        )


Step = Annotated[FrameStep | OrderStep | DayStep, Field(discriminator="kind")]


class ReplayDataset(InputModel):
    format_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=160)
    seed: ReplaySeed
    policy: TradingSettings
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    settlement_days: list[SettlementDaySettings] = Field(default_factory=list, max_length=100)
    repeat_daily: bool = False
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)

    def plan(self, ledger: LedgerSettings) -> SettlementPlan | None:
        if not self.settlement_days:
            if self.repeat_daily:
                raise ValueError("重复日结缺少结算计划")
            return None
        return SettlementSettings(
            days=tuple(self.settlement_days),
            repeat_daily=self.repeat_daily,
            report_dir=Path("unused-replay"),
        ).plan(ledger)

    def commands(self) -> tuple[Command, ...]:
        return tuple(step.command() for step in self.steps)


class ReplayRequest(InputModel):
    dataset: ReplayDataset
    candidate_policy: TradingSettings | None = None

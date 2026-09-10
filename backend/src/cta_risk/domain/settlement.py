"""收盘命令与冻结清算计划，不依赖网络或存储。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from cta_risk.domain.session import TradingSession


@dataclass(frozen=True)
class SettlementDay:
    session: TradingSession
    prices: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class SettlementPlan:
    days: tuple[SettlementDay, ...]


@dataclass(frozen=True)
class DayCommand:
    action: Literal["close", "settle", "open_day"]
    trading_day: date
    prices: tuple[tuple[str, Decimal], ...] = ()


@dataclass(frozen=True)
class DayResult:
    trading_day: date
    phase: str
    duplicate: bool = False

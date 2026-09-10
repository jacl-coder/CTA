"""收盘命令与冻结清算计划，不依赖网络或存储。"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.session import TradingSession


@dataclass(frozen=True)
class SettlementDay:
    session: TradingSession
    prices: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class SettlementPlan:
    days: tuple[SettlementDay, ...]
    repeat_daily: bool = False

    def day(self, trading_day: date) -> SettlementDay:
        if self.repeat_daily:
            template = self.days[0]
            index = (trading_day - template.session.trading_day).days
            if index >= 0:
                return SettlementDay(
                    TradingSession(trading_day, (index + 1) * template.session.close_sequence),
                    template.prices,
                )
        for day in self.days:
            if day.session.trading_day == trading_day:
                return day
        raise AccountingError("交易日不在冻结清算计划中")

    def next_day(self, trading_day: date) -> SettlementDay | None:
        current = self.day(trading_day)
        if self.repeat_daily:
            return self.day(trading_day + timedelta(days=1))
        index = self.days.index(current) + 1
        return self.days[index] if index < len(self.days) else None

    def visible_days(self, trading_day: date) -> tuple[SettlementDay, ...]:
        if not self.repeat_daily:
            return self.days
        upcoming = self.next_day(trading_day)
        assert upcoming is not None
        return self.day(trading_day), upcoming


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

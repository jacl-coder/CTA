"""独立结算价和交易日清算计划；不默认使用末笔行情替代结算价。"""

from pathlib import Path

from pydantic import field_validator

from cta_risk.config.ledger import LedgerModel, LedgerSettings
from cta_risk.config.session import SessionSettings
from cta_risk.domain.numbers import decimal_value
from cta_risk.domain.session import validate_sessions
from cta_risk.domain.settlement import SettlementDay, SettlementPlan


class SettlementDaySettings(SessionSettings):
    prices: dict[str, str]


class SettlementSettings(LedgerModel):
    days: tuple[SettlementDaySettings, ...]
    auto_settle: bool = True
    report_dir: Path
    repeat_daily: bool = False

    @field_validator("days", mode="before")
    @classmethod
    def as_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    def plan(self, ledger: LedgerSettings) -> SettlementPlan:
        definition = ledger.definition()
        sessions = tuple(item.to_domain() for item in self.days)
        validate_sessions(sessions, definition.trading_day)
        if self.repeat_daily and len(self.days) != 1:
            raise ValueError("持续日结只接受一个交易日模板，结算价按模板重复")
        instruments = {item.instrument_id: item for item in definition.instruments}
        days = []
        for item in self.days:
            if item.prices.keys() != instruments.keys():
                raise ValueError("每个交易日必须显式提供全部合约结算价")
            prices = tuple(
                sorted((key, decimal_value(value)) for key, value in item.prices.items())
            )
            for key, price in prices:
                instruments[key].validate_price(price)
            days.append(SettlementDay(item.to_domain(), prices))
        return SettlementPlan(tuple(days), self.repeat_daily)

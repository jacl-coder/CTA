"""会话边界由场景声明，不按自然日推断夜盘。"""

from datetime import date

from cta_risk.config.ledger import LedgerModel
from cta_risk.domain.session import TradingSession


class SessionSettings(LedgerModel):
    trading_day: str
    close_sequence: int

    def to_domain(self) -> TradingSession:
        day = date.fromisoformat(self.trading_day)
        if day.isoformat() != self.trading_day:
            raise ValueError("交易日必须使用 YYYY-MM-DD")
        return TradingSession(day, self.close_sequence)

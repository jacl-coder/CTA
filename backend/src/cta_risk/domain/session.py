"""显式交易日边界；序号贯穿运行，避免跨日重用源序号。"""

from dataclasses import dataclass
from datetime import date

from cta_risk.domain.errors import AccountingError


@dataclass(frozen=True)
class TradingSession:
    trading_day: date
    close_sequence: int

    def __post_init__(self) -> None:
        if type(self.trading_day) is not date or type(self.close_sequence) is not int:
            raise AccountingError("交易日和收盘帧类型非法")
        if self.close_sequence < 1:
            raise AccountingError("收盘帧必须为正整数")


def validate_sessions(sessions: tuple[TradingSession, ...], first_day: date) -> None:
    if not sessions or sessions[0].trading_day != first_day:
        raise AccountingError("会话首日必须与初始化交易日一致")
    for previous, current in zip(sessions, sessions[1:], strict=False):
        if (
            current.trading_day <= previous.trading_day
            or current.close_sequence <= previous.close_sequence
        ):
            raise AccountingError("交易日与收盘帧必须严格递增")

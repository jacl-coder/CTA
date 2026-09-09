"""骨架阶段明确不可交易，不能将 HTTP 存活误认为风控引擎就绪。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemStatus:
    trading_available: bool = False
    reasons: tuple[str, ...] = ("行情、账户和风控引擎尚未接入，当前不能交易。",)


def get_system_status() -> SystemStatus:
    return SystemStatus()

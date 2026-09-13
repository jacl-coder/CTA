"""同一工作台的演示预设，共用账户、品种和风控参数。"""

from pathlib import Path

from pydantic import Field

from cta_risk.config.ledger import LedgerModel
from cta_risk.config.market import MarketSettings
from cta_risk.config.settlement import SettlementSettings


class DemoSettings(LedgerModel):
    manual_price_age_seconds: int = Field(default=300, ge=1, le=3600)
    directory: Path
    manual_settlement: SettlementSettings
    fault_market: MarketSettings
    fault_settlement: SettlementSettings

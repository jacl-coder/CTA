"""仅声明已经由工程骨架使用的配置，业务参数随模块实现增加。"""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cta_risk.config.demo import DemoSettings
from cta_risk.config.ledger import LedgerSettings
from cta_risk.config.market import MarketSettings
from cta_risk.config.settlement import SettlementSettings
from cta_risk.config.trading import TradingSettings
from cta_risk.domain.session import TradingSession


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ServerSettings(StrictModel):
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1024, le=65535)


class Settings(StrictModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    # Explicit resource path override; absence uses packaged frontend resources.
    frontend_dir: Path | None = None
    ledger: LedgerSettings | None = None
    trading: TradingSettings | None = None
    market: MarketSettings | None = None
    settlement: SettlementSettings | None = None

    demo: DemoSettings | None = None

    @model_validator(mode="after")
    def trading_requires_ledger(self) -> Self:
        if self.settlement is not None:
            if self.trading is None or self.ledger is None:
                raise ValueError("启用日结必须配置账本与交易")
            plan = self.settlement.plan(self.ledger)
            if self.settlement.repeat_daily and (
                self.market is None or not self.market.continuous or not self.settlement.auto_settle
            ):
                raise ValueError("持续日结需要持续行情并开启自动清算")
            if self.market is not None:
                market = self.market.plan(self.ledger)
                if market.continuous != self.settlement.repeat_daily:
                    raise ValueError("持续行情必须配套持续日结")
                sessions = market.sessions or (
                    TradingSession(market.trading_day, market.frame_count),
                )
                if tuple(day.session for day in plan.days) != sessions:
                    raise ValueError("日结会话必须与行情会话边界完全一致")
            elif self.settlement.auto_settle:
                raise ValueError("自动收盘清算需要行情逻辑时钟，手动模式需关闭 auto_settle")
        elif self.market is not None and (self.market.sessions or self.market.continuous):
            raise ValueError("多交易日行情必须配置日结流程")
        if self.market is not None:
            if self.trading is None or self.ledger is None:
                raise ValueError("启用行情必须配置交易与账本")
            self.market.plan(self.ledger)
            if self.server.port in {source.port for source in self.market.sources}:
                raise ValueError("行情源端口不能与主服务相同")
        if self.trading is not None:
            if self.ledger is None:
                raise ValueError("启用交易必须配置账本")
            definition = self.ledger.definition()
            accounts = {item.account_id for item in definition.accounts}
            products = {item.product_id for item in definition.instruments}
            for limit in self.trading.policy().product_limits:
                if limit.account_id not in accounts or limit.product_id not in products:
                    raise ValueError("敞口限额引用未知账户或品种")
        if self.demo is not None:
            if not (self.ledger and self.trading and self.market and self.market.continuous):
                raise ValueError("场景选择需要持续行情、账户和交易配置")
            for preset_market, preset_settlement in (
                (None, self.demo.manual_settlement),
                (self.demo.fault_market, self.demo.fault_settlement),
            ):
                Settings(
                    server=self.server,
                    ledger=self.ledger,
                    trading=self.trading,
                    market=preset_market,
                    settlement=preset_settlement,
                )
            if self.demo.manual_settlement.auto_settle:
                raise ValueError("手工演示必须使用手工清算")
            if self.demo.fault_market.continuous or not self.demo.fault_settlement.auto_settle:
                raise ValueError("故障演示必须使用有限行情和自动清算")
        return self

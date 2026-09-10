"""仅声明已经由工程骨架使用的配置，业务参数随模块实现增加。"""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    @model_validator(mode="after")
    def trading_requires_ledger(self) -> Self:
        if self.settlement is not None:
            if self.trading is None or self.ledger is None:
                raise ValueError("启用日结必须配置账本与交易")
            plan = self.settlement.plan(self.ledger)
            if self.market is not None:
                market = self.market.plan(self.ledger)
                sessions = market.sessions or (
                    TradingSession(market.trading_day, market.frame_count),
                )
                if tuple(day.session for day in plan.days) != sessions:
                    raise ValueError("日结会话必须与行情会话边界完全一致")
            elif self.settlement.auto_settle:
                raise ValueError("自动收盘清算需要行情逻辑时钟，手动模式需关闭 auto_settle")
        elif self.market is not None and self.market.sessions:
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
        return self

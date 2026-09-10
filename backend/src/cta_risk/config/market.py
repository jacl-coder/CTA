"""双源场景、传输及故障配置。传输和故障设置不修改历史价格事实。"""

from typing import Self

from pydantic import Field, field_validator, model_validator

from cta_risk.config.ledger import LedgerModel, LedgerSettings
from cta_risk.config.session import SessionSettings
from cta_risk.domain.market import MarketPlan, SourcePlan
from cta_risk.domain.numbers import decimal_value
from cta_risk.domain.trading import PriceFrame


class PointSettings(LedgerModel):
    sequence: int
    prices: dict[str, str]


class OutageSettings(LedgerModel):
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end < self.start:
            raise ValueError("故障结束帧不能早于开始帧")
        return self


class SourceSettings(LedgerModel):
    source_id: str
    port: int = Field(ge=1024, le=65535)
    instruments: tuple[str, ...]
    points: tuple[PointSettings, ...]
    outages: tuple[OutageSettings, ...] = ()
    delay_ms: int = Field(default=0, ge=0, le=10000)

    @field_validator("instruments", "points", "outages", mode="before")
    @classmethod
    def as_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class MarketSettings(LedgerModel):
    sources: tuple[SourceSettings, ...]
    interval_ms: int = Field(default=250, ge=50, le=10000)
    frame_count: int = Field(default=240, ge=2, le=10000)
    poll_ms: int = Field(default=50, ge=10, le=1000)
    timeout_ms: int = Field(default=500, ge=50, le=5000)
    batch_size: int = Field(default=64, ge=1, le=256)
    managed_sources: bool = True
    sessions: tuple[SessionSettings, ...] = ()
    continuous: bool = False
    seed: int = 1
    amplitude_ticks: int = Field(default=20, ge=1, le=1000)

    @field_validator("sources", "sessions", mode="before")
    @classmethod
    def as_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    def plan(self, ledger: LedgerSettings) -> MarketPlan:
        definition = ledger.definition()
        plan = MarketPlan(
            definition.run_id,
            definition.trading_day,
            tuple(
                SourcePlan(
                    source.source_id,
                    tuple(sorted(source.instruments)),
                    tuple(
                        PriceFrame(
                            point.sequence,
                            definition.trading_day,
                            tuple(
                                sorted(
                                    (key, decimal_value(value))
                                    for key, value in point.prices.items()
                                )
                            ),
                            source.source_id,
                        )
                        for point in source.points
                    ),
                )
                for source in self.sources
            ),
            self.interval_ms,
            self.frame_count,
            tuple(item.to_domain() for item in self.sessions),
            self.continuous,
            self.seed,
            self.amplitude_ticks,
            tuple(sorted((item.instrument_id, item.tick_size) for item in definition.instruments))
            if self.continuous
            else (),
        )
        instruments = {item.instrument_id: item for item in definition.instruments}
        provided = {key for source in plan.sources for key in source.instruments}
        if provided != instruments.keys():
            raise ValueError("行情源合约覆盖必须与账本配置完全一致")
        for source in plan.sources:
            for point in source.points:
                for key, price in point.prices:
                    instruments[key].validate_price(price, execution=True)
        if len({source.port for source in self.sources}) != len(self.sources):
            raise ValueError("独立行情源不能使用相同端口")
        if any(
            outage.end > self.frame_count for source in self.sources for outage in source.outages
        ):
            raise ValueError("故障窗口超出演示范围")
        return plan

"""可复现双源行情的业务定义；不依赖网络、配置库或数据库。"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import identifier
from cta_risk.domain.numbers import financial_context
from cta_risk.domain.session import TradingSession, validate_sessions
from cta_risk.domain.trading import PriceFrame


def canonical(value: object) -> str:
    def encode(item: object) -> str:
        if isinstance(item, (date, Decimal)):
            return str(item)
        raise TypeError(type(item).__name__)

    return json.dumps(value, default=encode, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class SourcePlan:
    source_id: str
    instruments: tuple[str, ...]
    points: tuple[PriceFrame, ...]

    def __post_init__(self) -> None:
        identifier(self.source_id)
        if not self.instruments or tuple(sorted(set(self.instruments))) != self.instruments:
            raise AccountingError("源合约必须排序且不重复")
        if not self.points or self.points[0].sequence != 1:
            raise AccountingError("每个源必须配置第 1 帧价格")
        previous = 0
        for point in self.points:
            if (
                point.sequence <= previous
                or tuple(key for key, _ in point.prices) != self.instruments
            ):
                raise AccountingError("场景关键帧需递增且包含源的全部合约")
            previous = point.sequence


@dataclass(frozen=True)
class MarketPlan:
    run_id: str
    trading_day: date
    sources: tuple[SourcePlan, ...]
    interval_ms: int = 250
    frame_count: int = 240
    sessions: tuple[TradingSession, ...] = ()
    continuous: bool = False
    seed: int = 1
    amplitude_ticks: int = 20
    tick_sizes: tuple[tuple[str, Decimal], ...] = ()

    def __post_init__(self) -> None:
        identifier(self.run_id)
        if self.sessions:
            validate_sessions(self.sessions, self.trading_day)
            if self.sessions[-1].close_sequence != self.frame_count:
                raise AccountingError("最后会话收盘帧必须等于场景帧数")
        if type(self.trading_day) is not date or len(self.sources) < 2:
            raise AccountingError("多源运行至少需要两个源和明确交易日")
        if len({item.source_id for item in self.sources}) != len(self.sources):
            raise AccountingError("行情源标识重复")
        if type(self.interval_ms) is not int or not 50 <= self.interval_ms <= 10000:
            raise AccountingError("行情间隔必须为 50 至 10000 毫秒")
        if type(self.frame_count) is not int or not 2 <= self.frame_count <= 10000:
            raise AccountingError("演示帧数必须为 2 至 10000")
        instruments = [key for source in self.sources for key in source.instruments]
        if len(set(instruments)) != len(instruments):
            raise AccountingError("分工覆盖的行情源不能重复提供同一合约")
        if self.continuous:
            if len(self.sessions) != 1 or any(len(source.points) != 1 for source in self.sources):
                raise AccountingError("持续模拟需要一个交易日模板和每源一个初始价格节点")
            if type(self.seed) is not int or type(self.amplitude_ticks) is not int:
                raise AccountingError("模拟种子和波动幅度必须为整数")
            if not 1 <= self.amplitude_ticks <= 1000:
                raise AccountingError("波动幅度必须为 1 至 1000 个最小变动单位")
            ticks = dict(self.tick_sizes)
            if len(ticks) != len(self.tick_sizes) or set(ticks) != set(instruments):
                raise AccountingError("持续模拟必须提供全部合约最小变动单位")
            for source in self.sources:
                for key, price in source.points[0].prices:
                    if ticks[key] <= 0 or price <= ticks[key] * self.amplitude_ticks:
                        raise AccountingError("模拟价格波动范围必须保持为正")
        for source in self.sources:
            if source.points[-1].sequence > self.frame_count:
                raise AccountingError("场景关键帧超出演示范围")
            if any(point.trading_day != self.trading_day for point in source.points):
                raise AccountingError("场景交易日不一致")

    def to_json(self) -> str:
        raw = asdict(self)
        if not self.sessions:
            raw.pop("sessions")  # Keep existing single-day frozen configurations compatible.
        if not self.continuous:
            for key in ("continuous", "seed", "amplitude_ticks", "tick_sizes"):
                raw.pop(key)  # Existing finite runs retain their frozen JSON and hashes.
        return canonical(raw)

    def day_at(self, sequence: int) -> date:
        if self.continuous:
            # These are accelerated, consecutive simulated dates, not an exchange calendar.
            return self.trading_day + timedelta(days=(sequence - 1) // self.frame_count)
        return next(
            (item.trading_day for item in self.sessions if sequence <= item.close_sequence),
            self.trading_day,
        )

    def sequence_at(self, epoch_ms: int, now_ms: int) -> int:
        sequence = max(0, (now_ms - epoch_ms) // self.interval_ms + 1)
        return sequence if self.continuous else min(self.frame_count, sequence)

    def completed(self, epoch_ms: int, now_ms: int) -> bool:
        return not self.continuous and now_ms >= epoch_ms + self.frame_count * self.interval_ms


@dataclass(frozen=True)
class SourceFrame:
    run_id: str
    source_id: str
    trading_day: date
    sequence: int
    event_ms: int
    prices: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        identifier(self.run_id)
        identifier(self.source_id)
        PriceFrame(self.sequence, self.trading_day, self.prices, self.source_id)
        if type(self.event_ms) is not int or self.event_ms < 0:
            raise AccountingError("行情事件时间非法")

    def payload(self) -> str:
        return canonical(asdict(self))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.payload().encode()).hexdigest()

    @classmethod
    def from_json(cls, payload: str) -> "SourceFrame":
        raw = json.loads(payload)
        return cls(
            raw["run_id"],
            raw["source_id"],
            date.fromisoformat(raw["trading_day"]),
            raw["sequence"],
            raw["event_ms"],
            tuple((key, Decimal(value)) for key, value in raw["prices"]),
        )


def generate_frame(
    plan: MarketPlan, source: SourcePlan, epoch_ms: int, sequence: int
) -> SourceFrame:
    if (
        type(sequence) is not int
        or sequence < 1
        or (not plan.continuous and sequence > plan.frame_count)
    ):
        raise AccountingError("请求帧超出场景范围")
    point = next(item for item in reversed(source.points) if item.sequence <= sequence)
    prices = point.prices
    if plan.continuous:
        ticks = dict(plan.tick_sizes)

        def offset(instrument: str, anchor: int) -> int:
            if anchor == 0:
                return 0  # First frame is exactly the configured initial price.
            key = canonical((plan.seed, source.source_id, instrument, anchor))
            value = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
            return value % (2 * plan.amplitude_ticks + 1) - plan.amplitude_ticks

        # Interpolate integer ticks between seeded anchors: bounded, smooth and O(1) per frame.
        # History/restart never depend on process-local random state or traversal order.
        anchor, step = divmod(sequence - 1, 16)
        with financial_context():
            prices = tuple(
                (
                    key,
                    price
                    + ticks[key]
                    * ((offset(key, anchor) * (16 - step) + offset(key, anchor + 1) * step) // 16),
                )
                for key, price in prices
            )
    return SourceFrame(
        plan.run_id,
        source.source_id,
        plan.day_at(sequence),
        sequence,
        epoch_ms + (sequence - 1) * plan.interval_ms,
        prices,
    )

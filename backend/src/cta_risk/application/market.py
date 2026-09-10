"""多源原始帧、应用进度和控制命令；财务变更仍由账本队列执行。"""

from dataclasses import dataclass

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.market import MarketPlan, SourceFrame, generate_frame
from cta_risk.domain.trading import PriceFrame


@dataclass(frozen=True)
class EventObservation:
    event_id: int
    occurred_ms: int
    detected_ms: int
    recovered: bool


@dataclass(frozen=True)
class StoredMarket:
    epoch_ms: int
    applied_sequence: int
    frames: tuple[SourceFrame, ...] = ()
    observations: tuple[EventObservation, ...] = ()


@dataclass(frozen=True)
class SourceStatus:
    source_id: str
    connected: bool = False
    head: int = 0
    reason: str = "等待连接"


@dataclass(frozen=True)
class SourceBatch:
    frames: tuple[SourceFrame, ...]


@dataclass(frozen=True)
class ApplyMarketFrame:
    recovered: bool


@dataclass(frozen=True)
class MarketGate:
    sources: tuple[SourceStatus, ...]
    ready: bool
    reason: str


MarketCommand = SourceBatch | ApplyMarketFrame | MarketGate


def validate_frame(plan: MarketPlan, epoch_ms: int, frame: SourceFrame) -> None:
    source = next((item for item in plan.sources if item.source_id == frame.source_id), None)
    if source is None:
        raise AccountingError("未知行情源")
    # Simulator history is immutable and independently reproducible from the frozen scenario.
    # A real provider adapter would validate its protocol instead of this scenario equality.
    if frame != generate_frame(plan, source, epoch_ms, frame.sequence):
        raise AccountingError("行情与冻结场景不一致（运行、交易日、时间或价格错误）")


def contiguous(frames: tuple[SourceFrame, ...], source_id: str, applied: int = 0) -> int:
    sequences = {item.sequence for item in frames if item.source_id == source_id}
    cursor = applied  # Committed complete frames already form a verified contiguous prefix.
    while cursor + 1 in sequences:
        cursor += 1
    return cursor


def assemble(plan: MarketPlan, stored: StoredMarket, sequence: int) -> PriceFrame:
    frames = [item for item in stored.frames if item.sequence == sequence]
    if {item.source_id for item in frames} != {source.source_id for source in plan.sources}:
        raise AccountingError("缺少行情源，不能应用不完整帧")
    return PriceFrame(
        sequence,
        plan.day_at(sequence),
        tuple(sorted(pair for item in frames for pair in item.prices)),
        "multi-source",
    )

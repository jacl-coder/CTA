"""有界 HTTP 行情协议；输入边界严格校验并核对内容哈希。"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.market import SourceFrame


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WireFrame(WireModel):
    run_id: str
    source_id: str
    trading_day: str
    sequence: int
    event_ms: int
    prices: dict[str, str]
    digest: str

    @classmethod
    def from_domain(cls, frame: SourceFrame) -> "WireFrame":
        return cls(
            run_id=frame.run_id,
            source_id=frame.source_id,
            trading_day=str(frame.trading_day),
            sequence=frame.sequence,
            event_ms=frame.event_ms,
            prices={key: str(value) for key, value in frame.prices},
            digest=frame.digest,
        )

    def to_domain(self) -> SourceFrame:
        frame = SourceFrame(
            self.run_id,
            self.source_id,
            date.fromisoformat(self.trading_day),
            self.sequence,
            self.event_ms,
            tuple(sorted((key, Decimal(value)) for key, value in self.prices.items())),
        )
        if frame.digest != self.digest:
            raise AccountingError("行情内容哈希不一致")
        return frame


class HeadResponse(WireModel):
    run_id: str
    source_id: str
    epoch_ms: int
    sequence: int
    complete: bool
    latest: WireFrame | None


class HistoryResponse(WireModel):
    run_id: str
    source_id: str
    epoch_ms: int
    start: int
    end: int
    head: int
    next_sequence: int
    frames: list[WireFrame]

"""两源并发观测、有界补拉；仅完整帧通过应用队列更新风险。"""

import asyncio
import logging
from contextlib import suppress

import httpx

from cta_risk.application.contracts import LedgerBusy, LedgerUnavailable
from cta_risk.application.ledger import LedgerService
from cta_risk.application.market import (
    ApplyMarketFrame,
    MarketGate,
    SourceBatch,
    SourceStatus,
    contiguous,
)
from cta_risk.config.market import MarketSettings, SourceSettings
from cta_risk.domain.errors import AccountingError
from cta_risk.marketdata.protocol import HeadResponse, HistoryResponse

logger = logging.getLogger(__name__)


class MarketFeed:
    def __init__(
        self,
        service: LedgerService,
        config: MarketSettings,
        client: httpx.AsyncClient | None = None,
    ):
        self.service = service
        self.config = config
        self.client = client or httpx.AsyncClient(timeout=config.timeout_ms / 1000, trust_env=False)
        self._owns_client = client is None
        self._task: asyncio.Task[None] | None = None
        self._recovering = service.source_state().applied_sequence > 0
        self._statuses = tuple(SourceStatus(item.source_id) for item in config.sources)
        self._last_error = ""

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="cta-market-feed")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        if self._owns_client:
            await self.client.aclose()

    def _identity(self, run_id: str, source_id: str, epoch_ms: int, source: SourceSettings) -> None:
        if (run_id, source_id, epoch_ms) != (
            self.service.definition.run_id,
            source.source_id,
            self.service.source_state().epoch_ms,
        ):
            raise AccountingError("行情源运行身份或逻辑起点不一致")

    async def _head(self, source: SourceSettings) -> HeadResponse:
        response = await self.client.get(f"http://127.0.0.1:{source.port}/head")
        response.raise_for_status()
        head = HeadResponse.model_validate(response.json())
        self._identity(head.run_id, head.source_id, head.epoch_ms, source)
        plan = self.service.market_plan
        assert plan is not None
        previous = max(
            (
                item.sequence
                for item in self.service.source_state().frames
                if item.source_id == source.source_id
            ),
            default=0,
        )
        if head.sequence < previous or (not plan.continuous and head.sequence > plan.frame_count):
            raise AccountingError("源进度回退或超出场景范围")
        if (head.sequence == 0) != (head.latest is None):
            raise AccountingError("源进度与最新帧缺失状态矛盾")
        if head.latest is not None:
            frame = head.latest.to_domain()
            if frame.sequence != head.sequence or frame.source_id != source.source_id:
                raise AccountingError("源最新帧与进度不一致")
        return head

    async def _history(self, source: SourceSettings, start: int, target: int) -> None:
        response = await self.client.get(
            f"http://127.0.0.1:{source.port}/history",
            params={"start": start, "end": target, "limit": self.config.batch_size},
        )
        response.raise_for_status()
        result = HistoryResponse.model_validate(response.json())
        self._identity(result.run_id, result.source_id, result.epoch_ms, source)
        end = min(target, start + self.config.batch_size - 1)
        if (
            result.start != start
            or result.end != end
            or result.next_sequence != end + 1
            or result.head < end
            or len(result.frames) != end - start + 1
            or [frame.sequence for frame in result.frames] != list(range(start, end + 1))
        ):
            raise AccountingError("历史补拉范围不连续或不完整")
        frames = tuple(item.to_domain() for item in result.frames)
        if any(item.source_id != source.source_id for item in frames):
            raise AccountingError("历史响应混入其他行情源")
        await self.service.market_command(SourceBatch(frames))

    async def cycle(self) -> None:
        results = await asyncio.gather(
            *(self._head(source) for source in self.config.sources), return_exceptions=True
        )
        statuses = []
        heads = []
        for source, result in zip(self.config.sources, results, strict=True):
            if isinstance(result, BaseException):
                statuses.append(SourceStatus(source.source_id, False, reason=type(result).__name__))
            else:
                statuses.append(SourceStatus(source.source_id, True, result.sequence, ""))
                heads.append(result)
        self._statuses = tuple(statuses)
        if len(heads) != len(self.config.sources):
            self._recovering = True
            await self.service.market_command(
                MarketGate(self._statuses, False, "行情源不可用，等待恢复并补全")
            )
            # Persist the healthy peer's later frames; never apply a partial cross-source frame.
            for head in heads:
                if head.latest is not None:
                    await self.service.market_command(SourceBatch((head.latest.to_domain(),)))
            return
        applied = self.service.source_state().applied_sequence
        target = min(head.sequence for head in heads)
        if max(head.sequence for head in heads) > applied + 1:
            self._recovering = True
        if target > applied:
            await self.service.market_command(
                MarketGate(self._statuses, False, "正在补齐并按顺序处理完整行情帧")
            )
        for head in heads:
            if head.latest is not None:
                await self.service.market_command(SourceBatch((head.latest.to_domain(),)))
        for source in self.config.sources:
            cursor = contiguous(self.service.source_state().frames, source.source_id, applied)
            if cursor < target:
                # One overlapping frame deliberately verifies retries against immutable stored data.
                start = max(1, cursor) if self.config.batch_size > 1 else cursor + 1
                await self._history(source, start, target)
        available = min(
            contiguous(self.service.source_state().frames, item.source_id, applied)
            for item in self.config.sources
        )
        for _ in range(min(self.config.batch_size, available - applied)):
            if not self.service.market_frame_allowed(
                self.service.source_state().applied_sequence + 1
            ):
                break  # Next-day prices wait until the previous day is settled and rolled over.
            await self.service.market_command(ApplyMarketFrame(self._recovering))
        caught_up = self.service.source_state().applied_sequence >= max(
            head.sequence for head in heads
        )
        await self.service.market_command(
            MarketGate(self._statuses, caught_up, "" if caught_up else "历史行情尚未补齐，暂停交易")
        )
        if caught_up:
            self._recovering = False

    async def _run(self) -> None:
        while self.service.available:
            try:
                await self.cycle()
                self._last_error = ""
            except LedgerUnavailable:
                return
            except Exception as exc:
                self._recovering = True
                message = f"{type(exc).__name__}: {exc}"
                if message != self._last_error:
                    logger.warning("行情接入暂停 reason=%s", message)
                    self._last_error = message
                try:
                    await self.service.market_command(
                        MarketGate(self._statuses, False, "行情接入或补全失败，等待重试")
                    )
                except LedgerBusy:
                    pass  # Expected-frame and price-age gates still block stale trading.
                except LedgerUnavailable:
                    return
            await asyncio.sleep(self.config.poll_ms / 1000)

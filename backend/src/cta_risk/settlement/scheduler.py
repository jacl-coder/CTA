"""收盘调度与报告文件重试。财务变更始终提交给应用队列。"""

import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path

from cta_risk.application.contracts import LedgerBusy, LedgerUnavailable
from cta_risk.application.ledger import LedgerService
from cta_risk.config.settlement import SettlementSettings
from cta_risk.domain.settlement import DayCommand
from cta_risk.report.exporter import export_report

logger = logging.getLogger(__name__)


class SettlementScheduler:
    def __init__(self, service: LedgerService, settings: SettlementSettings):
        self.service = service
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._exports: dict[str, tuple[Path, Path]] = {}
        self.errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def export(self, payload: str) -> tuple[Path, Path]:
        day = json.loads(payload)["trading_day"]
        async with self._lock:
            try:
                task = asyncio.create_task(
                    asyncio.to_thread(export_report, payload, self.settings.report_dir)
                )
                try:
                    paths = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
            except OSError as exc:
                self.errors[day] = str(exc)
                raise
            self._exports[day] = paths
            self.errors.pop(day, None)
            return paths

    async def cycle(self) -> None:
        service = self.service
        state = service.trading_state()
        assert service.settlement_plan is not None
        if self.settings.auto_settle:
            assert service.market_plan is not None
            days = service.settlement_plan.days
            index = next(
                i for i, day in enumerate(days) if day.session.trading_day == state.trading_day
            )
            day = days[index]
            # The same injected market clock protects orders at the exact boundary.
            if service.session_has_closed():
                if state.phase == "OPEN":
                    await service.day_command(DayCommand("close", state.trading_day))
                if (
                    service.trading_state().phase == "CLOSING"
                    and len(service.trading_state().frames) == day.session.close_sequence
                ):
                    await service.day_command(DayCommand("settle", state.trading_day, day.prices))
                if service.trading_state().phase == "SETTLED" and index + 1 < len(days):
                    await service.day_command(
                        DayCommand("open_day", days[index + 1].session.trading_day)
                    )
        for payload in service.trading_state().reports:
            day_key = json.loads(payload)["trading_day"]
            if day_key not in self._exports:
                try:
                    await self.export(payload)
                except OSError:
                    logger.warning("日报导出失败，可重试 day=%s", day_key)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="cta-settlement-scheduler")

    async def _run(self) -> None:
        while self.service.available:
            try:
                await self.cycle()
            except LedgerBusy:
                pass
            except LedgerUnavailable:
                return
            await asyncio.sleep(0.2 if self.errors else 0.05)

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        # Drain a possible to_thread export before the enclosing temporary run is removed.
        async with self._lock:
            pass

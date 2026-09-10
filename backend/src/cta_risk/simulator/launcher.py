"""托管两个独立模拟源进程；重启沿用数据库中冻结的逻辑起点。"""

import asyncio
import logging
import os
import sys
import time
from contextlib import suppress
from typing import IO

import yaml

from cta_risk.config.models import Settings


class SourceProcesses:
    def __init__(self, settings: Settings, epoch_ms: int):
        assert settings.ledger is not None and settings.market is not None
        self.settings = settings
        self.epoch_ms = epoch_ms
        self.directory = settings.ledger.database.parent / "sources"
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.logs: dict[str, IO[bytes]] = {}
        self.last_start: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        # The same immutable business config is used by every source; DB is never opened there.
        (self.directory / "runtime.yaml").write_text(
            yaml.safe_dump(self.settings.model_dump(mode="json", exclude_none=True)),
            encoding="utf-8",
        )
        await self.ensure_running()
        self._task = asyncio.create_task(self._watch(), name="cta-source-supervisor")

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                await self.ensure_running()
            except OSError:
                logging.getLogger(__name__).exception("模拟源重启失败")

    async def ensure_running(self) -> None:
        assert self.settings.market is not None
        for source in self.settings.market.sources:
            previous = self.processes.get(source.source_id)
            if previous is not None and previous.returncode is None:
                continue
            if time.monotonic() - self.last_start.get(source.source_id, 0) < 1:
                continue
            if source.source_id not in self.logs:
                # Index prevents source identifiers from becoming filesystem paths.
                index = len(self.logs)
                self.logs[source.source_id] = (self.directory / f"source-{index}.log").open("ab")
            prefix = (
                [sys.executable]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "cta_risk"]
            )
            environment = dict(os.environ)
            if getattr(sys, "frozen", False):
                environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            self.processes[source.source_id] = await asyncio.create_subprocess_exec(
                *prefix,
                "market-source",
                "--config",
                str(self.directory / "runtime.yaml"),
                "--source-id",
                source.source_id,
                "--epoch-ms",
                str(self.epoch_ms),
                "--parent-pid",
                str(os.getpid()),
                stdout=self.logs[source.source_id],
                stderr=asyncio.subprocess.STDOUT,
                env=environment,
            )
            self.last_start[source.source_id] = time.monotonic()

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        for process in self.processes.values():
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
        for process in self.processes.values():
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        for log in self.logs.values():
            log.close()

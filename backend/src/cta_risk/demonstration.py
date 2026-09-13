"""切换场景时替换整组资源，以独立账本保存每次演示和当前运行。"""

import asyncio
import hashlib
import os
import socket
from contextlib import AsyncExitStack
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from cta_risk.config.models import Settings
from cta_risk.domain.trading import CommandConflict
from cta_risk.runtime import running

Mode = Literal["continuous", "manual", "fault"]


class ActiveRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Mode
    template_hash: str
    settings: Settings


class DemoStatus(BaseModel):
    mode: Mode
    initial_prices: dict[str, str]


class DemoController:
    def __init__(self, app: FastAPI, settings: Settings):
        assert settings.demo is not None
        self.app = app
        self.base = settings
        self.directory = settings.demo.directory
        self.manifest = self.directory / "active.json"
        self.template_hash = hashlib.sha256(settings.model_dump_json().encode()).hexdigest()
        self.active: ActiveRun | None = None
        self.stack = AsyncExitStack()
        self.lock = asyncio.Lock()
        self.switching = False

    def status(self) -> DemoStatus:
        assert self.active is not None and self.base.market is not None
        return DemoStatus(
            mode=self.active.mode,
            initial_prices={
                key: value
                for source in self.base.market.sources
                for key, value in source.points[0].prices.items()
            },
        )

    def new_run(self, mode: Mode) -> ActiveRun:
        assert self.base.demo and self.base.ledger and self.base.trading
        identifier = f"{mode}-{uuid4().hex}"
        directory = self.directory / identifier
        ledger = self.base.ledger.model_copy(
            update={
                "database": directory / "ledger.sqlite3",
                "run_id": identifier,
            }
        )
        market = self.base.market
        settlement = self.base.settlement
        if mode == "manual":
            market = None
            settlement = self.base.demo.manual_settlement
        elif mode == "fault":
            market = self.base.demo.fault_market
            settlement = self.base.demo.fault_settlement
        assert settlement is not None
        settings = Settings(
            server=self.base.server,
            ledger=ledger,
            trading=self.base.trading.model_copy(
                update={"max_price_age_seconds": self.base.demo.manual_price_age_seconds}
            )
            if mode == "manual"
            else self.base.trading,
            market=market,
            settlement=settlement.model_copy(update={"report_dir": directory / "reports"}),
        )
        return ActiveRun(mode=mode, settings=settings, template_hash=self.template_hash)

    def persist(self, active: ActiveRun) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(active.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.manifest)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def check_ports(settings: Settings) -> None:
        if settings.market is None or not settings.market.managed_sources:
            return
        for source in settings.market.sources:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", source.port))

    async def start(self) -> None:
        if self.manifest.exists():
            active = ActiveRun.model_validate_json(self.manifest.read_text(encoding="utf-8"))
            if active.template_hash != self.template_hash:
                raise ValueError("默认配置已变更，请恢复原配置或使用新的 demo.directory 创建运行")
        else:
            active = self.new_run("continuous")
        self.check_ports(active.settings)
        await self.stack.enter_async_context(running(self.app, active.settings))
        self.persist(active)
        self.active = active

    async def switch(self, mode: Mode) -> None:
        # The HTTP guard holds the lock until all old requests finish. No request can
        # capture an old service while its sources, scheduler and writer are closed.
        previous = self.active
        assert previous is not None
        target = self.new_run(mode)
        self.switching = True
        try:
            await self.stack.aclose()
            try:
                self.check_ports(target.settings)
                await self.stack.enter_async_context(running(self.app, target.settings))
                self.persist(target)
            except Exception:
                await self.stack.aclose()
                await self.stack.enter_async_context(running(self.app, previous.settings))
                raise
            self.active = target
        finally:
            self.switching = False

    def check_run(self, run_id: str | None) -> None:
        if not self.active or not self.active.settings.ledger:
            raise CommandConflict("场景尚未就绪")
        if run_id != self.active.settings.ledger.run_id:
            raise CommandConflict("场景已切换，请刷新后重新操作；旧场景请求未执行")

    async def close(self) -> None:
        async with self.lock:
            await self.stack.aclose()

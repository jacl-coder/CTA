"""应用层数据契约和存储端口；不依赖 HTTP、Pydantic 或 SQLite。"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from cta_risk.application.market import EventObservation, StoredMarket
from cta_risk.domain.market import SourceFrame
from cta_risk.domain.models import Account, AccountBook, CloseAllocation, Fill, Instrument


class LedgerError(RuntimeError):
    """账本生命周期、背压或存储错误。"""


class LedgerUnavailable(LedgerError):
    pass


class LedgerBusy(LedgerError):
    pass


class StorageError(LedgerError):
    pass


class ConfigurationMismatch(StorageError):
    pass


class AccountNotFound(LookupError):
    pass


@dataclass(frozen=True)
class RecordedFill:
    fill: Fill
    source: str
    fee: Decimal | None = None


@dataclass(frozen=True)
class RunDefinition:
    run_id: str
    trading_day: date
    accounts: tuple[Account, ...]
    instruments: tuple[Instrument, ...]
    initial_fills: tuple[RecordedFill, ...] = ()

    def canonical_json(self) -> str:
        def encode(value: object) -> str:
            if isinstance(value, (Decimal, date)):
                return str(value)
            raise TypeError(f"无法序列化 {type(value).__name__}")

        return json.dumps(asdict(self), default=encode, sort_keys=True, ensure_ascii=False)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass(frozen=True)
class StoredAccount:
    book: AccountBook
    revision: int


@dataclass(frozen=True)
class StoredLedger:
    accounts: tuple[StoredAccount, ...]
    records: tuple[RecordedFill, ...]


@dataclass(frozen=True)
class FillResult:
    fill_id: str
    account_id: str
    revision: int
    duplicate: bool


@dataclass(frozen=True)
class JournalEntry:
    revision: int
    command_json: str
    outcome_json: str


class LedgerStore(Protocol):
    """所有同步方法均由应用层的专用数据库线程调用。"""

    def initialize(self, definition: RunDefinition, seed: StoredLedger) -> StoredLedger: ...

    def save_fill(
        self, run_id: str, record: RecordedFill, book: AccountBook, expected_revision: int
    ) -> int: ...

    def close(self) -> None: ...

    def initialize_trading(
        self, run_id: str, policy_json: str | None
    ) -> tuple[JournalEntry, ...]: ...

    def read_journal(self, run_id: str, revision: int) -> tuple[JournalEntry, ...]: ...

    def save_transition(
        self,
        run_id: str,
        entry: JournalEntry,
        record: RecordedFill | None,
        book: AccountBook | None,
        expected_revision: int | None,
        market_sequence: int | None = None,
        observations: tuple[EventObservation, ...] = (),
    ) -> None: ...

    def initialize_market(
        self, run_id: str, plan_json: str | None, epoch_ms: int
    ) -> StoredMarket | None: ...

    def save_source_frames(self, run_id: str, frames: tuple[SourceFrame, ...]) -> None: ...

    def initialize_settlement(self, run_id: str, config_json: str | None) -> tuple[str, ...]: ...

    def save_day_transition(
        self,
        run_id: str,
        entry: JournalEntry,
        accounts: tuple[StoredAccount, ...],
        report_json: str | None,
    ) -> None: ...

    def verify_allocations(
        self, run_id: str, allocations: tuple[tuple[str, CloseAllocation], ...]
    ) -> None: ...

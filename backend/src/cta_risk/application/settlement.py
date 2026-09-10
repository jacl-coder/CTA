"""收盘、清算、下一交易日的可重放状态转换；只计算不进行 I/O。"""

import hashlib
import json
from dataclasses import dataclass, replace

from cta_risk.application.contracts import RunDefinition, StoredAccount, StoredLedger
from cta_risk.application.trading import TradingState, encode
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.numbers import total
from cta_risk.domain.settlement import DayCommand, DayResult, SettlementPlan
from cta_risk.domain.trading import CommandConflict, RiskPolicy
from cta_risk.risk.rules import RiskState
from cta_risk.settlement.calculator import settle_account, start_next_day
from cta_risk.valuation.calculator import value_account


@dataclass(frozen=True)
class DayTransition:
    state: TradingState
    command_json: str
    outcome_json: str
    result: DayResult
    report_json: str | None = None


def advance_day(
    state: TradingState,
    command: DayCommand,
    definition: RunDefinition,
    policy: RiskPolicy,
    plan: SettlementPlan,
) -> DayTransition:
    day = plan.day(command.trading_day)
    reports = [json.loads(item) for item in state.reports]
    existing = next(
        (item for item in reports if item["trading_day"] == str(command.trading_day)), None
    )
    if command.action == "settle":
        if command.prices != day.prices:
            raise CommandConflict("结算价与冻结计划不一致，拒绝覆盖或替换")
        if existing is not None:
            return DayTransition(state, "", "", DayResult(command.trading_day, "SETTLED", True))
    elif command.prices:
        raise AccountingError("只有清算命令可以包含结算价")
    if command.action == "close" and (
        existing is not None
        or (command.trading_day == state.trading_day and state.phase == "CLOSING")
    ):
        return DayTransition(
            state,
            "",
            "",
            DayResult(command.trading_day, "SETTLED" if existing else "CLOSING", True),
        )
    if command.action == "open_day" and command.trading_day in state.opened_days:
        return DayTransition(state, "", "", DayResult(command.trading_day, "OPEN", True))
    report_json = None
    if command.action == "open_day":
        if state.phase != "SETTLED" or plan.next_day(state.trading_day) != day:
            raise AccountingError("必须完成前日清算，且只能结转到计划中的下一交易日")
        accounts = tuple(
            StoredAccount(start_next_day(item.book, command.trading_day), item.revision + 1)
            for item in state.ledger.accounts
        )
        updated = replace(
            state,
            ledger=StoredLedger(accounts, state.ledger.records),
            phase="OPEN",
            close_sequence=None,
            risks=tuple(RiskState(item.book.account.account_id) for item in accounts),
            opened_days=(*state.opened_days, command.trading_day),
        )
    else:
        if command.trading_day != state.trading_day:
            raise AccountingError("只能操作当前交易日")
        if command.action == "close":
            if len(state.frames) > day.session.close_sequence:
                raise AccountingError("已处理超过收盘边界的行情")
            updated = replace(state, phase="CLOSING", close_sequence=day.session.close_sequence)
        elif command.action == "settle":
            if state.phase != "CLOSING" or len(state.frames) != day.session.close_sequence:
                raise AccountingError("必须先封账并补齐全部收盘行情后才能日结")
            accounts = tuple(
                StoredAccount(settle_account(item.book, dict(command.prices)), item.revision + 1)
                for item in state.ledger.accounts
            )
            risks = {item.account_id: item for item in state.risks}
            entries = []
            day_start = next(
                (item.sequence for item in state.frames if item.trading_day == state.trading_day), 1
            )
            for item in accounts:
                book = item.book
                entries.append(
                    {
                        "account_id": book.account.account_id,
                        "initial_capital": book.account.initial_capital,
                        "settlement": book.settlement,
                        "valuation": value_account(book, dict(command.prices)),
                        "risk": risks[book.account.account_id],
                        "risk_event_count": sum(
                            event.account_id == book.account.account_id
                            and event.frame_sequence >= day_start
                            for event in state.events
                        ),
                    }
                )
            totals = {}
            for key in (
                "opening_balance",
                "realized_pnl",
                "holding_pnl",
                "fees",
                "net_pnl",
                "closing_balance",
                "margin",
                "available",
            ):
                totals[key] = total(getattr(item.book.settlement, key) for item in accounts)
            for key in ("gross_exposure", "net_exposure"):
                totals[key] = total(
                    getattr(value_account(item.book, dict(command.prices)), key)
                    for item in accounts
                )
            report_json = encode(
                {
                    "run_id": definition.run_id,
                    "trading_day": command.trading_day,
                    "close_sequence": day.session.close_sequence,
                    "config_hash": definition.fingerprint,
                    "policy_hash": hashlib.sha256(encode(policy).encode()).hexdigest(),
                    "settlement_plan_hash": hashlib.sha256(encode(plan).encode()).hexdigest(),
                    "prices": dict(command.prices),
                    "accounts": entries,
                    "totals": totals,
                }
            )
            updated = replace(
                state,
                ledger=StoredLedger(accounts, state.ledger.records),
                phase="SETTLED",
                reports=(*state.reports, report_json),
            )
        else:
            raise AccountingError("未知日结命令")
    updated = replace(updated, revision=state.revision + 1)
    result = DayResult(command.trading_day, updated.phase)
    payload = encode(
        {"kind": command.action, "trading_day": command.trading_day, "prices": command.prices}
    )
    outcome = encode(
        {
            "result": result,
            "accounts": updated.ledger.accounts,
            "risks": updated.risks,
            "report": report_json,
        }
    )
    return DayTransition(updated, payload, outcome, result, report_json)

"""将一致版本的已提交日志导出为不含路径和凭据的历史数据。"""

import json

from cta_risk.application.ledger import LedgerService
from cta_risk.application.trading import encode
from cta_risk.replay.engine import state_digest
from cta_risk.replay.models import MAX_STEPS, ReplayDataset


async def export_current(service: LedgerService) -> ReplayDataset:
    state, journal = await service.history(MAX_STEPS)
    definition = service.definition
    seed = json.loads(definition.canonical_json())
    fills = []
    for record in definition.initial_fills:
        raw = json.loads(encode(record.fill))
        raw.pop("trading_day")
        fills.append({**raw, "source": record.source})
    seed["initial_fills"] = fills
    steps = []
    for entry in journal:
        command = json.loads(entry.command_json)
        if "prices" in command:
            command["prices"] = dict(command["prices"])
        steps.append(command)
    plan = service.settlement_plan
    return ReplayDataset.model_validate(
        {
            "name": f"{definition.run_id} · 截至第 {state.revision} 步",
            "seed": seed,
            "policy": json.loads(encode(service.policy)),
            "expected_digest": state_digest(state),
            "settlement_days": [
                {
                    "trading_day": str(day.session.trading_day),
                    "close_sequence": day.session.close_sequence,
                    "prices": {key: str(value) for key, value in day.prices},
                }
                for day in plan.days
            ]
            if plan
            else [],
            "repeat_daily": plan.repeat_daily if plan else False,
            "steps": steps,
        }
    )

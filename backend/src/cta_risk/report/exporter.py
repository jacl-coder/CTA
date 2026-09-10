"""仅展示冻结日结数据，不执行估值、清算或汇总计算。"""

import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

_SETTLEMENT_COLUMNS = (
    ("opening_balance", "期初权益"),
    ("realized_pnl", "已实现盈亏"),
    ("holding_pnl", "持仓盈亏"),
    ("fees", "手续费"),
    ("net_pnl", "净盈亏"),
    ("closing_balance", "期末权益"),
    ("margin", "保证金"),
    ("available", "可用资金"),
)
_EXPOSURE_COLUMNS = (("gross_exposure", "总敞口"), ("net_exposure", "净敞口"))
_VALUATION_COLUMNS = (
    ("floating_pnl", "浮动盈亏"),
    ("equity", "估值权益"),
    ("margin", "估值保证金"),
    ("available", "估值可用资金"),
    *_EXPOSURE_COLUMNS,
)
_POSITION_COLUMNS = (
    ("account_id", "账户"),
    ("instrument_id", "合约"),
    ("product_id", "品种"),
    ("side", "方向"),
    ("quantity", "数量"),
    ("notional", "名义敞口"),
    ("margin", "保证金"),
    ("floating_pnl", "浮动盈亏"),
)
_LINE_COLUMNS = (
    ("lot_id", "持仓批次"),
    ("instrument_id", "合约"),
    ("side", "方向"),
    ("quantity", "数量"),
    ("basis", "结算基准价"),
    ("settlement_price", "结算价"),
    ("pnl", "结算盈亏"),
)


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    head = "".join(f'<th scope="col">{_text(header)}</th>' for header in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_text(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    if not body:
        body = f'<tr><td colspan="{len(headers)}">无记录</td></tr>'
    return (
        '<div class="table-wrap"><table><thead><tr>'
        f"{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _records(columns: Sequence[tuple[str, str]], rows: Iterable[dict[str, Any]]) -> str:
    return _table(
        [label for _, label in columns], ([row[key] for key, _ in columns] for row in rows)
    )


def _render(raw: dict[str, Any], trading_day: str) -> str:
    accounts = raw["accounts"]
    sections = [
        f"<h1>交易风控日报 · {_text(trading_day)}</h1>",
        "<p>数据来源：已冻结日结快照。金额与汇总均按快照原文展示。</p>",
        "<h2>规则 / 配置追溯</h2>",
        _table(
            ["追溯项目", "冻结值"],
            [
                ["运行标识（run_id）", raw["run_id"]],
                ["交易日（trading_day）", raw["trading_day"]],
                ["日结序号（close_sequence）", raw["close_sequence"]],
                ["配置哈希（config_hash）", raw["config_hash"]],
                ["规则哈希（policy_hash）", raw["policy_hash"]],
            ],
        ),
        "<h2>资金与敞口汇总</h2>",
        _records((*_SETTLEMENT_COLUMNS, *_EXPOSURE_COLUMNS), [raw["totals"]]),
        "<h2>冻结结算价格</h2>",
        _table(["合约", "结算价"], raw["prices"].items()),
        "<h2>账户总览</h2>",
    ]
    account_rows = []
    for account in accounts:
        settlement, valuation, risk = account["settlement"], account["valuation"], account["risk"]
        account_rows.append(
            [account["account_id"], account["initial_capital"]]
            + [settlement[key] for key, _ in _SETTLEMENT_COLUMNS]
            + [valuation[key] for key, _ in _EXPOSURE_COLUMNS]
            + [
                "是" if risk["warning"] else "否",
                "是" if risk["circuit_broken"] else "否",
                "、".join(risk["restricted_products"]) or "无",
                account["risk_event_count"],
            ]
        )
    sections.append(
        _table(
            ["账户", "初始资金"]
            + [label for _, label in (*_SETTLEMENT_COLUMNS, *_EXPOSURE_COLUMNS)]
            + ["风险预警", "已熔断", "受限品种", "风险事件数"],
            account_rows,
        )
    )
    for account in accounts:
        settlement, valuation = account["settlement"], account["valuation"]
        sections.extend(
            [
                f"<section><h2>账户：{_text(account['account_id'])}</h2>",
                "<h3>持仓估值与敞口</h3>",
                _records(_VALUATION_COLUMNS, [valuation]),
                _records(_POSITION_COLUMNS, valuation["positions"]),
                "<h3>结算明细</h3>",
                _table(
                    ["结算账户", "结算交易日"],
                    [[settlement["account_id"], settlement["trading_day"]]],
                ),
                _records(_SETTLEMENT_COLUMNS, [settlement]),
                _records(_LINE_COLUMNS, settlement["lines"]),
                "<h3>账户结算价格</h3>",
                _table(["合约", "结算价"], settlement["prices"]),
                "</section>",
            ]
        )
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>交易风控日报 · {_text(trading_day)}</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;color:#182230;background:#fff;"
        "margin:2rem;line-height:1.6}h1,h2,h3{color:#163d63}"
        ".table-wrap{overflow-x:auto;margin:1rem 0 2rem}"
        "table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}"
        "th,td{border:1px solid #cbd5e1;padding:.5rem .75rem;text-align:left;"
        "white-space:pre-wrap;overflow-wrap:anywhere}th{background:#edf2f7}"
        "section{border-top:2px solid #cbd5e1;margin-top:2rem}"
        "@media print{body{margin:0;font-size:9pt}.table-wrap{overflow:visible}"
        "tr{break-inside:avoid}thead{display:table-header-group}}"
        "</style></head><body><main>" + "\n".join(sections) + "</main></body></html>\n"
    )


def export_report(payload: str, directory: Path) -> tuple[Path, Path]:
    """返回 (HTML 路径, JSON 路径)，文件名仅取经过校验并规范化的交易日。

    JSON 原文以 UTF-8 保存，金额不做数值转换。全部内容先生成并写入同目录
    临时文件，再用 os.replace 分别原子覆盖；两文件之间不提供事务保证。
    同一冻结 payload 可安全重试。解析、渲染和文件系统异常直接向上抛出。
    """
    raw = json.loads(payload)
    trading_day = date.fromisoformat(raw["trading_day"]).isoformat()
    html = _render(raw, trading_day)
    html_path = directory / f"risk-{trading_day}.html"
    json_path = directory / f"risk-{trading_day}.json"
    directory.mkdir(parents=True, exist_ok=True)
    temporary_paths: list[Path] = []
    try:
        for destination, content in ((html_path, html), (json_path, payload)):
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=directory,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_paths.append(Path(temporary.name))
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
        for temporary_path, destination in zip(
            temporary_paths, (html_path, json_path), strict=True
        ):
            os.replace(temporary_path, destination)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)
    return html_path, json_path

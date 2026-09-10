"""独立人工冻结快照；不调用账本、估值或清算生成测试预期。"""

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from cta_risk.report import export_report
from cta_risk.report import exporter as module


@pytest.fixture
def payload() -> str:
    # Deliberately distinct aggregate values verify that rendering never recomputes totals.
    return """{
  "run_id": "run/../人工快照",
  "trading_day": "2026-09-09",
  "close_sequence": 27,
  "config_hash": "config<&>hash",
  "policy_hash": "policy-冻结规则",
  "prices": {"SHFE.rb2610": "3520.0000"},
  "accounts": [{
    "account_id": "账户<script>alert('x')</script>&\\\"",
    "initial_capital": "9007199254740993.0100",
    "settlement": {
      "account_id": "账户<script>alert('x')</script>&\\\"",
      "trading_day": "2026-09-09",
      "opening_balance": "9007199254740993.0100",
      "realized_pnl": "10.0200",
      "holding_pnl": "-20.0300",
      "fees": "1.0400",
      "net_pnl": "-11.0500",
      "closing_balance": "9007199254740981.9600",
      "margin": "352.0000",
      "available": "9007199254740629.9600",
      "prices": [["SHFE.rb2610", "3520.0000"]],
      "lines": [{
        "lot_id": "批次<一>", "instrument_id": "SHFE.rb2610", "side": "LONG",
        "quantity": 1, "basis": "3522.0030", "settlement_price": "3520.0000",
        "pnl": "-20.0300"
      }]
    },
    "valuation": {
      "positions": [{
        "account_id": "账户<script>alert('x')</script>&\\\"",
        "instrument_id": "SHFE.rb2610", "product_id": "SHFE.rb", "side": "LONG",
        "quantity": 1, "notional": "35200.0000", "margin": "352.0000",
        "floating_pnl": "-20.0300"
      }],
      "floating_pnl": "-20.0300", "equity": "9007199254740981.9600",
      "margin": "352.0000", "available": "9007199254740629.9600",
      "gross_exposure": "35200.0000", "net_exposure": "35200.0000"
    },
    "risk": {"warning": true, "circuit_broken": false, "restricted_products": ["SHFE.rb"]},
    "risk_event_count": 3
  }],
  "totals": {
    "opening_balance": "100.0001", "realized_pnl": "101.0002",
    "holding_pnl": "102.0003", "fees": "103.0004", "net_pnl": "104.0005",
    "closing_balance": "105.0006", "margin": "106.0007", "available": "107.0008",
    "gross_exposure": "108.0009", "net_exposure": "-109.0010"
  }
}"""


class ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def test_exports_standalone_chinese_html_and_exact_json(payload: str, tmp_path: Path) -> None:
    directory = tmp_path / "上层指定" / "日报"
    html_path, json_path = export_report(payload, directory)
    assert html_path == directory / "risk-2026-09-09.html"
    assert json_path == directory / "risk-2026-09-09.json"
    assert json_path.read_bytes() == payload.encode("utf-8")
    html = html_path.read_text(encoding="utf-8")
    parser = ReportParser()
    parser.feed(html)
    raw = json.loads(payload)
    assert raw["accounts"][0]["account_id"] in parser.text
    assert "账户&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;&amp;&quot;" in html
    assert "script" not in parser.tags
    assert not {"script", "link", "iframe", "img"}.intersection(parser.tags)
    assert not any(key in {"src", "href"} for key, _ in parser.attributes)
    assert ("charset", "utf-8") in parser.attributes
    assert ("lang", "zh-CN") in parser.attributes
    for label in (
        "账户总览",
        "资金与敞口汇总",
        "持仓估值与敞口",
        "结算明细",
        "账户结算价格",
        "规则 / 配置追溯",
        "初始资金",
        "已熔断",
        "风险事件数",
    ):
        assert label in parser.text
    for value in ("27", "3", "是", "否", "SHFE.rb", "批次<一>", "3522.0030"):
        assert value in parser.text
    for field in ("run_id", "config_hash", "policy_hash"):
        assert raw[field] in parser.text
    for value in raw["totals"].values():
        assert value in parser.text
    account = raw["accounts"][0]
    for record in (account["settlement"], account["valuation"]):
        for value in record.values():
            if isinstance(value, str):
                assert value in parser.text
    assert account["initial_capital"] in parser.text
    assert "3520.0000" in parser.text
    assert set(directory.iterdir()) == {html_path, json_path}


def test_identical_retry_is_deterministic(payload: str, tmp_path: Path) -> None:
    paths = export_report(payload, tmp_path)
    original = [path.read_bytes() for path in paths]
    assert export_report(payload, tmp_path) == paths
    assert [path.read_bytes() for path in paths] == original
    assert set(tmp_path.iterdir()) == set(paths)


@pytest.mark.parametrize("trading_day", ["../../escape", "2026-02-30", "2026-09-09/evil", None])
def test_invalid_date_does_not_create_output(
    trading_day: object, payload: str, tmp_path: Path
) -> None:
    raw = json.loads(payload)
    raw["trading_day"] = trading_day
    directory = tmp_path / "output"
    with pytest.raises((TypeError, ValueError)):
        export_report(json.dumps(raw), directory)
    assert not directory.exists()


def test_filename_uses_normalized_date_only(payload: str, tmp_path: Path) -> None:
    raw = json.loads(payload)
    raw["trading_day"] = "20260909"
    paths = export_report(json.dumps(raw), tmp_path)
    assert [path.name for path in paths] == ["risk-2026-09-09.html", "risk-2026-09-09.json"]


def test_empty_accounts_positions_and_lines(payload: str, tmp_path: Path) -> None:
    raw = json.loads(payload)
    account = raw["accounts"][0]
    account["valuation"]["positions"] = []
    account["settlement"]["lines"] = []
    account["settlement"]["prices"] = []
    account["risk"]["restricted_products"] = []
    raw["prices"] = {}
    html_path, _ = export_report(json.dumps(raw), tmp_path)
    assert html_path.read_text(encoding="utf-8").count("无记录") == 4
    raw["accounts"] = []
    html_path, _ = export_report(json.dumps(raw), tmp_path)
    assert "无记录" in html_path.read_text(encoding="utf-8")


def test_invalid_payload_preserves_existing_files(payload: str, tmp_path: Path) -> None:
    paths = export_report(payload, tmp_path)
    original = [path.read_bytes() for path in paths]
    raw = json.loads(payload)
    del raw["accounts"][0]["valuation"]
    for invalid in ("{broken", json.dumps(raw)):
        with pytest.raises((ValueError, KeyError)):
            export_report(invalid, tmp_path)
        assert [path.read_bytes() for path in paths] == original
        assert set(tmp_path.iterdir()) == set(paths)


def test_directory_is_a_file(payload: str, tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"keep")
    with pytest.raises(OSError):
        export_report(payload, blocker)
    assert blocker.read_bytes() == b"keep"
    assert list(tmp_path.iterdir()) == [blocker]


@pytest.mark.parametrize("stage", ["mkdir", "create", "write", "flush", "fsync", "replace"])
@pytest.mark.parametrize("failed_file", [1, 2])
def test_failed_retry_keeps_complete_files_and_cleans_temporary_files(
    stage: str, failed_file: int, payload: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = export_report(payload, tmp_path)
    original = [path.read_bytes() for path in paths]
    create = module.tempfile.NamedTemporaryFile
    replace = module.os.replace
    fsync = module.os.fsync
    calls = 0

    def fail() -> None:
        raise OSError("injected export failure")

    def create_temporary(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if stage == "create" and calls == failed_file:
            fail()
        temporary = create(*args, **kwargs)
        if calls == failed_file:
            if stage == "write":
                write = temporary.write

                def partial_write(content: str) -> None:
                    write(content[:17])
                    fail()

                monkeypatch.setattr(temporary, "write", partial_write)
            elif stage == "flush":
                monkeypatch.setattr(temporary, "flush", fail)
        return temporary

    def failing_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == failed_file:
            fail()
        fsync(fd)

    def failing_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        # Replacement must receive a complete, closed file from the destination directory.
        assert source.parent == destination.parent
        assert source.read_bytes() == original[paths.index(destination)]
        if calls == failed_file:
            fail()
        replace(source, destination)

    with monkeypatch.context() as patch:
        if stage == "mkdir":
            patch.setattr(Path, "mkdir", lambda *args, **kwargs: fail())
        elif stage == "replace":
            patch.setattr(module.os, "replace", failing_replace)
        elif stage == "fsync":
            patch.setattr(module.os, "fsync", failing_fsync)
        else:
            patch.setattr(module.tempfile, "NamedTemporaryFile", create_temporary)
        with pytest.raises(OSError, match="injected export failure"):
            export_report(payload, tmp_path)
    assert [path.read_bytes() for path in paths] == original
    assert set(tmp_path.iterdir()) == set(paths)
    assert export_report(payload, tmp_path) == paths
    assert [path.read_bytes() for path in paths] == original

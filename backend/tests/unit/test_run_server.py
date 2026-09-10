"""启动入口的进程归属边界：不能把包装器和其他项目误当作旧服务。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("run_server", ROOT / "scripts/run_server.py")
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.mark.parametrize(
    "args, expected",
    [
        ([str(ROOT / "backend/.venv/bin/cta-risk"), "serve"], "serve"),
        ([str(ROOT / "backend/.venv/bin/python3"), "-m", "cta_risk", "serve"], "serve"),
        ([str(ROOT / "backend/.venv/bin/python3"), "-m", "http.server", "8004"], None),
        (["uv", "run", "cta-risk", "serve"], None),
        (["/bin/sh", "-c", "cta-risk serve"], None),
        (["/other/backend/.venv/bin/python3", "-m", "cta_risk", "serve"], None),
    ],
)
def test_only_recognizes_this_project_cli(args: list[str], expected: str | None) -> None:
    assert runner.project_command(args, ROOT) == expected


def test_stops_source_owner_and_database_owner_but_leaves_other_servers() -> None:
    parent = runner.Process(1, 0, "serve", "1", frozenset({"/old/ledger.sqlite3.lock"}))
    source = runner.Process(2, 1, "market-source", "2", frozenset({"socket:[100]"}))
    same_database = runner.Process(3, 0, "serve", "3", frozenset({"/new/ledger.sqlite3.lock"}))
    unrelated = runner.Process(4, 0, "serve", "4", frozenset({"socket:[200]"}))
    assert runner.conflicting_processes(
        [parent, source, same_database, unrelated], {"socket:[100]"}, Path("/new/ledger.sqlite3")
    ) == [parent, same_database]

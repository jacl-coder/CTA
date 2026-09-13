"""两个真实 HTTP 行情进程的补数、熔断、崩溃恢复与发布包验收。"""

import argparse
import os
import signal
import socket
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import yaml
from demo_risk import request, server


def free_ports() -> list[int]:
    with ExitStack() as stack:
        listeners = [stack.enter_context(socket.socket()) for _ in range(3)]
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        return [item.getsockname()[1] for item in listeners]


def source_children(pid: int) -> list[int]:
    """本项目 Linux 发布目标：只操作本脚本启动的后端所拥有的行情子进程。"""
    children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    return [
        int(child)
        for child in children
        if b"market-source" in Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")
    ]


def wait_sources_exit(pids: list[int]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        alive = []
        for pid in pids:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0]
            except FileNotFoundError:
                continue
            if state != "Z":
                alive.append(pid)
        if not alive:
            return
        time.sleep(0.05)
    raise AssertionError(f"后端退出后行情子进程未退出: {alive}")


def snapshot(base: str) -> dict[str, Any]:
    return {
        path: request(base, f"/api/{path}")
        for path in ("accounts", "positions", "trades", "risk", "risk-events", "orders", "market")
    }


def run(root: Path, directory: Path, packaged: bool, interrupted: bool) -> dict[str, Any]:
    directory.mkdir()
    ports = free_ports()
    base = f"http://127.0.0.1:{ports[0]}"
    raw = yaml.safe_load((root / "testdata/configs/market-demo.yaml").read_text(encoding="utf-8"))
    raw["server"]["port"] = ports[0]
    raw["ledger"]["database"] = "ledger.sqlite3"
    raw["market"]["interval_ms"] = 200
    for source, port in zip(raw["market"]["sources"], ports[1:], strict=True):
        source["port"] = port
        if not interrupted:
            source["outages"] = []
    config = directory / "demo.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    if packaged:
        executable = root / "dist/cta-risk/cta-risk"
        if not executable.is_file():
            raise SystemExit("请先运行 make package")
        command = [str(executable)]
        environment = {"PATH": str(directory / "no-executables"), "LANG": "C.UTF-8"}
    else:
        command = [sys.executable, "-m", "cta_risk"]
        environment = dict(os.environ)
    command += ["serve", "--config", str(config)]
    restarted = killed_source = resumed = False
    outages: set[str] = set()
    epoch = None
    with ExitStack() as stack:
        process = stack.enter_context(server(command, directory, base, environment, "initial"))
        token = request(base, "/api/session")["token"]
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            status = request(base, "/api/market/sources")
            if epoch is None:
                epoch = status["epoch_ms"]
            assert status["epoch_ms"] == epoch
            sequence = status["expected_sequence"]
            if status["market_ready"]:
                assert status["applied_sequence"] == sequence
                if interrupted and 21 <= sequence < 30:
                    resumed = True
            if interrupted and sequence >= 8:
                for source in status["sources"]:
                    if not source["connected"]:
                        outages.add(source["source_id"])
            if interrupted and not restarted and 9 <= sequence <= 15:
                assert not status["market_ready"]
                before = request(base, "/api/trades")
                for offset in ("OPEN", "CLOSE_TODAY"):
                    denied = request(
                        base,
                        "/api/orders",
                        {
                            "request_id": f"during-outage-{offset}",
                            "account_id": "A",
                            "instrument_id": "SHFE.rb2610",
                            "trading_day": "2026-09-09",
                            "side": "LONG",
                            "offset": offset,
                            "quantity": 1,
                        },
                        token,
                    )
                    assert denied["reason"] == "MARKET_UNAVAILABLE"
                assert request(base, "/api/trades") == before
                children = source_children(process.pid)
                assert len(children) == 2
                process.kill()
                process.wait(timeout=5)
                wait_sources_exit(children)
                process = stack.enter_context(
                    server(command, directory, base, environment, "during-outage-restart")
                )
                token = request(base, "/api/session")["token"]
                restarted = True
                print("A 源中断：开平仓暂停；强制结束后端及重启，两个旧源退出，逻辑起点保持")
            if interrupted and not killed_source and 30 <= sequence <= 35:
                children = source_children(process.pid)
                victim = next(
                    pid
                    for pid in children
                    if b"source-B" in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                )
                os.kill(victim, signal.SIGKILL)
                killed_source = True
            if status["completed"] and status["applied_sequence"] == 64:
                break
            time.sleep(0.04)
        else:
            raise AssertionError(f"行情未按期补齐: {status}")
        assert not status["market_ready"]
        assert status["duplicate_frames"] > 0
        assert all(
            item["contiguous_sequence"] == item["received_count"] == 64
            for item in status["sources"]
        )
        current = snapshot(base)
        breakers = [item for item in current["risk-events"] if item["kind"] == "CIRCUIT_BREAK"]
        assert [(item["account_id"], item["frame_sequence"]) for item in breakers] == [
            ("A", 10),
            ("B", 32),
        ]
        for item in breakers:
            assert item["occurred_ms"] == epoch + (item["frame_sequence"] - 1) * 200
            assert item["detected_ms"] >= item["occurred_ms"]
            if interrupted:
                assert item["recovered"]
        if interrupted:
            assert restarted and killed_source and resumed
            assert outages == {"source-A", "source-B"}
            assert len(source_children(process.pid)) == 2
            print("B 源独立退出后自动重启；两源均补齐 64 帧，A/10 与 B/32 的历史熔断各一次")
            children = source_children(process.pid)
            process.terminate()
            process.wait(timeout=5)
            wait_sources_exit(children)
            stack.enter_context(server(command, directory, base, environment, "completed-restart"))
            assert snapshot(base) == current
            restored = request(base, "/api/market/sources")
            assert restored["epoch_ms"] == epoch and restored["applied_sequence"] == 64
            assert all(item["received_count"] == 64 for item in restored["sources"])
            print("场景结束后重启：账本、行情、拒单记录及风险事件完全一致")
        return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaged", action="store_true", help="验证 PyInstaller 发布包")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="cta-market-demo-") as folder:
        directory = Path(folder)
        try:
            continuous = run(root, directory / "continuous", args.packaged, False)
            interrupted = run(root, directory / "interrupted", args.packaged, True)
            for key in ("accounts", "positions", "trades", "risk", "market"):
                assert continuous[key] == interrupted[key], key

            # Detection time and recovered flag describe delivery, not business risk semantics.
            def events(result):
                return [
                    {
                        key: value
                        for key, value in item.items()
                        if key not in ("occurred_ms", "detected_ms", "recovered")
                    }
                    for item in result["risk-events"]
                ]

            assert events(continuous) == events(interrupted)
        except Exception:
            for log in sorted(directory.rglob("*.log")):
                print(f"{log}:\n{log.read_text(errors='replace')[-6000:]}", file=sys.stderr)
            raise
    print("双源真实 HTTP 验收通过：连续与中断场景的账本、最终风险和业务风险事件一致。")


if __name__ == "__main__":
    main()

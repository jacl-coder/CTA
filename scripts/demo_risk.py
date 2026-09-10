"""原题熔断场景的可重复 HTTP 演示；每次使用独立临时数据库。"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml


def request(
    base: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    expected: int = 200,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        base + path, headers=headers, data=json.dumps(body).encode() if body is not None else None
    )
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        payload = json.load(response)
        if response.status != expected:
            raise RuntimeError(f"{path}: {response.status}, {payload}")
        return payload


@contextmanager
def server(
    command: list[str], directory: Path, base: str, environment: dict[str, str], label: str
) -> Iterator[subprocess.Popen[bytes]]:
    with (directory / f"{label}.log").open("w+") as log:
        process = subprocess.Popen(
            command, cwd=directory, env=environment, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    request(base, "/api/health/live")
                    break
                except urllib.error.URLError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("演示服务启动超时")
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaged", action="store_true", help="验证 PyInstaller 发布包")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="cta-risk-demo-") as folder:
        directory = Path(folder)
        raw = yaml.safe_load((root / "configs/trading-demo.yaml").read_text(encoding="utf-8"))
        raw["server"]["port"] = port
        raw["ledger"]["database"] = "ledger.sqlite3"
        config = directory / "demo.yaml"
        config.write_text(yaml.safe_dump(raw), encoding="utf-8")
        if args.packaged:
            executable = root / "dist/cta-risk/cta-risk"
            if not executable.is_file():
                raise SystemExit("请先运行 make package")
            command = [str(executable)]
            environment = {"PATH": str(directory / "no-executables"), "LANG": "C.UTF-8"}
        else:
            command = [sys.executable, "-m", "cta_risk"]
            environment = dict(os.environ)
        command += ["serve", "--config", str(config)]
        order = {
            "request_id": "blocked",
            "account_id": "A",
            "instrument_id": "SHFE.rb2610",
            "trading_day": "2026-09-09",
            "side": "LONG",
            "offset": "OPEN",
            "quantity": 1,
        }
        prices = {"SHFE.rb2610": "3351", "DCE.i2701": "800", "CFFEX.IF2609": "4000"}

        def publish(sequence: int, rb: str, token: str) -> None:
            prices["SHFE.rb2610"] = rb
            request(
                base,
                "/api/simulation/frames",
                {"sequence": sequence, "trading_day": "2026-09-09", "prices": prices},
                token,
            )

        with server(command, directory, base, environment, "initial") as process:
            token = request(base, "/api/session")["token"]
            publish(1, "3351", token)
            risk = request(base, "/api/risk?account_id=A")[0]
            assert risk["floating_pnl"] == "-2980.00" and not risk["circuit_broken"]
            print("浮亏 2980 元：告警，尚未熔断")
            publish(2, "3350", token)
            risk = request(base, "/api/risk?account_id=A")[0]
            assert risk["floating_pnl"] == "-3000.00" and risk["circuit_broken"]
            before = request(base, "/api/trades")
            denied = request(base, "/api/orders", order, token)
            assert denied["reason"] == "CIRCUIT_BROKEN"
            assert request(base, "/api/trades") == before
            print("浮亏达到 3000 元（初始资金 3%）：熔断，实际开仓请求被拒绝，无新增成交")
            publish(3, "3450", token)
            assert (
                request(base, "/api/orders", {**order, "request_id": "rebound"}, token)["reason"]
                == "CIRCUIT_BROKEN"
            )
            closed = request(
                base,
                "/api/orders",
                {**order, "request_id": "close", "offset": "CLOSE_TODAY"},
                token,
            )
            assert closed["status"] == "FILLED" and closed["fee"] == "2.00"
            b = request(
                base,
                "/api/orders",
                {
                    **order,
                    "request_id": "B-open",
                    "account_id": "B",
                    "instrument_id": "DCE.i2701",
                    "side": "SHORT",
                },
                token,
            )
            assert b["status"] == "FILLED"
            print("价格反弹后仍限制 A 开仓；A 平仓成功，B 正常开仓")
            accounts = request(base, "/api/accounts")
            trades = request(base, "/api/trades")
            events = request(base, "/api/risk-events")
            assert sum(item["kind"] == "CIRCUIT_BREAK" for item in events) == 1
            process.kill()
            process.wait(timeout=5)
        with server(command, directory, base, environment, "restarted"):
            assert request(base, "/api/accounts") == accounts
            assert request(base, "/api/trades") == trades
            assert request(base, "/api/risk-events") == events
            assert request(base, "/api/risk?account_id=A")[0]["circuit_broken"]
            request(base, "/api/health/ready", expected=503)
            token = request(base, "/api/session")["token"]
            retry = request(
                base,
                "/api/orders",
                {**order, "request_id": "close", "offset": "CLOSE_TODAY"},
                token,
            )
            assert retry["duplicate"] and retry["fill_id"] == closed["fill_id"]
            publish(4, "3500", token)
            assert (
                request(base, "/api/orders", {**order, "request_id": "after-restart"}, token)[
                    "reason"
                ]
                == "CIRCUIT_BROKEN"
            )
            assert request(base, "/api/trades") == trades
            print("强制退出后重启：账本、风险事件和熔断恢复；重试不重复成交，新增开仓仍被拒绝")
    print("模拟交易熔断闭环演示通过；双源行情与中断补全另见 make demo-market。")


if __name__ == "__main__":
    main()

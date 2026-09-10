"""真实双源两日清算、平昨、日报与跨日进程恢复验收。"""

import argparse
import json
import os
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import yaml
from demo_market import free_ports, source_children, wait_sources_exit
from demo_risk import request, server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaged", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="cta-settlement-demo-") as folder:
        directory = Path(folder)
        ports = free_ports()
        base = f"http://127.0.0.1:{ports[0]}"
        raw = yaml.safe_load((root / "configs/settlement-demo.yaml").read_text())
        raw["server"]["port"] = ports[0]
        raw["ledger"]["database"] = "ledger.sqlite3"
        raw["settlement"]["report_dir"] = "reports"
        raw["market"]["interval_ms"] = 200
        for source, port in zip(raw["market"]["sources"], ports[1:], strict=True):
            source["port"] = port
        config = directory / "demo.yaml"
        config.write_text(yaml.safe_dump(raw))
        if args.packaged:
            command = [str(root / "dist/cta-risk/cta-risk")]
            environment = {"PATH": str(directory / "no-executables"), "LANG": "C.UTF-8"}
        else:
            command = [sys.executable, "-m", "cta_risk"]
            environment = dict(os.environ)
        command += ["serve", "--config", str(config)]
        try:
            with ExitStack() as stack:
                process = stack.enter_context(
                    server(command, directory, base, environment, "first")
                )
                token = request(base, "/api/session")["token"]
                deadline = time.monotonic() + 40
                closed = None
                attempts = 0
                first = None
                while time.monotonic() < deadline:
                    status = request(base, "/api/settlement/status")
                    if (
                        status["trading_day"] == "2026-09-10"
                        and status["market_ready"]
                        and closed is None
                    ):
                        first = request(base, "/api/reports/2026-09-09")
                        positions = request(base, "/api/positions?account_id=A")
                        assert positions[0]["yesterday_quantity"] == 2
                        assert positions[0]["today_quantity"] == 0
                        assert not request(base, "/api/risk?account_id=A")[0]["circuit_broken"]
                        attempts += 1
                        closed = request(
                            base,
                            "/api/orders",
                            {
                                "request_id": f"day2-close-yesterday-{attempts}",
                                "account_id": "A",
                                "instrument_id": "SHFE.rb2610",
                                "trading_day": "2026-09-10",
                                "side": "LONG",
                                "offset": "CLOSE_YESTERDAY",
                                "quantity": 1,
                            },
                            token,
                        )
                        if closed["reason"] == "MARKET_UNAVAILABLE":
                            closed = None
                            time.sleep(0.02)
                            continue
                        assert closed["status"] == "FILLED" and closed["price"] == "3520"
                        assert request(base, "/api/accounts/A")["realized_pnl"] == "100.00"
                        print(
                            "首日自动封账清算，次日昨仓 2 手；"
                            "首日熔断重置后重新评估，平昨 1 手盈亏 100 元"
                        )
                        children = source_children(process.pid)
                        assert len(children) == 2
                        process.kill()
                        process.wait(timeout=5)
                        wait_sources_exit(children)
                        process = stack.enter_context(
                            server(command, directory, base, environment, "day2-restart")
                        )
                        assert request(base, "/api/reports/2026-09-09") == first
                        token = request(base, "/api/session")["token"]
                        retry = request(
                            base,
                            "/api/orders",
                            {
                                "request_id": f"day2-close-yesterday-{attempts}",
                                "account_id": "A",
                                "instrument_id": "SHFE.rb2610",
                                "trading_day": "2026-09-10",
                                "side": "LONG",
                                "offset": "CLOSE_YESTERDAY",
                                "quantity": 1,
                            },
                            token,
                        )
                        assert retry["duplicate"] and retry["fill_id"] == closed["fill_id"]
                        print("跨日成交后强制退出并恢复：首日报告不变，重试平昨不重复成交")
                    if status["trading_day"] == "2026-09-10" and status["phase"] == "SETTLED":
                        break
                    time.sleep(0.02)
                else:
                    raise AssertionError(f"未完成两日日结: {status}")
                assert closed is not None and first is not None
                second = request(base, "/api/reports/2026-09-10")
                expected = {
                    "2026-09-09": {
                        "A": ("292.00", "100292.00", "7020.00"),
                        "B": ("-2006.00", "197994.00", "19440.00"),
                        "C": ("1495.00", "1001495.00", "144180.00"),
                    },
                    "2026-09-10": {
                        "A": ("-102.00", "100190.00", "3490.00"),
                        "B": ("2000.00", "199994.00", "19200.00"),
                        "C": ("1500.00", "1002995.00", "144360.00"),
                    },
                }
                for report in (first, second):
                    day = report["trading_day"]
                    for account in report["accounts"]:
                        settled = account["settlement"]
                        assert (
                            tuple(settled[key] for key in ("net_pnl", "closing_balance", "margin"))
                            == expected[day][account["account_id"]]
                        )
                    result = request(
                        base,
                        "/api/settlement/settle",
                        {"trading_day": day, "prices": report["prices"]},
                        token,
                    )
                    assert result["duplicate"]
                    files = request(base, f"/api/reports/{day}/export", {}, token)
                    assert json.loads((directory / "reports" / files["json"]).read_text()) == report
                    html = (directory / "reports" / files["html"]).read_text()
                    assert "交易风控日报" in html and "https://" not in html
                before = request(base, "/api/trades")
                denied = request(
                    base,
                    "/api/orders",
                    {
                        "request_id": "after-settle",
                        "account_id": "A",
                        "instrument_id": "SHFE.rb2610",
                        "trading_day": "2026-09-10",
                        "side": "LONG",
                        "offset": "OPEN",
                        "quantity": 1,
                    },
                    token,
                )
                assert (
                    denied["reason"] == "TRADING_DAY_CLOSED"
                    and request(base, "/api/trades") == before
                )
                accounts, events = request(base, "/api/accounts"), request(base, "/api/risk-events")
                children = source_children(process.pid)
                process.terminate()
                process.wait(timeout=5)
                wait_sources_exit(children)
                stack.enter_context(
                    server(command, directory, base, environment, "settled-restart")
                )
                assert request(base, "/api/accounts") == accounts
                assert request(base, "/api/risk-events") == events
                assert request(base, "/api/settlement/status")["phase"] == "SETTLED"
                assert request(base, "/api/reports/2026-09-10") == second
                print(
                    "两日三个账户的盈亏、余额与保证金均符合独立手算；"
                    "重复清算无重复入账，HTML/JSON 与冻结快照一致"
                )
        except Exception:
            for log in sorted(directory.rglob("*.log")):
                print(f"{log}:\n{log.read_text(errors='replace')[-4000:]}", file=sys.stderr)
            raise
    print("真实双源两日日结与日报验收通过。")


if __name__ == "__main__":
    main()

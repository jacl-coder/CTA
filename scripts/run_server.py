"""Makefile 的本机启动入口：正常替换本项目中资源冲突的服务，保留账本。"""

import argparse
import os
import select
import signal
import socket
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import NamedTuple

from cta_risk.config.loader import ConfigurationError, load_settings

ROOT = Path(__file__).resolve().parents[1]


class Process(NamedTuple):
    pid: int
    parent: int
    command: str
    started: str
    handles: frozenset[str]


def project_command(args: list[str], cwd: Path) -> str | None:
    """只认本项目虚拟环境的直接 CLI 调用，排除 uv、shell 和其他应用。"""
    if not args:
        return None

    def absolute(value: str) -> Path:
        path = Path(value)
        return Path(os.path.abspath(cwd / path))

    interpreter = absolute(args[0])
    if interpreter.parent != ROOT / "backend/.venv/bin":
        return None
    if interpreter.name == "cta-risk":
        tail = args[1:]
    elif interpreter.name.startswith("python"):
        if args[1:3] == ["-m", "cta_risk"] and cwd == ROOT:
            tail = args[3:]
        elif len(args) > 1 and absolute(args[1]) == ROOT / "backend/.venv/bin/cta-risk":
            tail = args[2:]
        else:
            return None
    else:
        return None
    if tail and tail[0] in {"serve", "market-source"}:
        return tail[0]
    return None


def process_state(pid: int) -> tuple[int, str]:
    # comm 字段可以包含空格或括号，后面的 ppid / starttime 才是稳定身份信息。
    fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return int(fields[1]), fields[19]


def project_processes() -> list[Process]:
    result = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = entry.joinpath("cmdline").read_bytes().decode().rstrip("\0").split("\0")
            command = project_command(args, entry.joinpath("cwd").resolve(strict=True))
            if command is None:
                continue
            pid = int(entry.name)
            parent, started = process_state(pid)
            handles = set()
            for fd in entry.joinpath("fd").iterdir():
                with suppress(FileNotFoundError):
                    handles.add(os.readlink(fd))
            result.append(Process(pid, parent, command, started, frozenset(handles)))
        except (OSError, UnicodeError):
            continue  # 进程已经退出，或不属于当前用户。
    return result


def listening_sockets(ports: set[int]) -> set[str]:
    result = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not table.exists():
            continue
        for row in table.read_text().splitlines()[1:]:
            fields = row.split()
            if fields[3] == "0A" and int(fields[1].split(":")[1], 16) in ports:
                result.add(f"socket:[{fields[9]}]")
    return result


def conflicting_processes(
    processes: list[Process], sockets: set[str], database: Path | None
) -> list[Process]:
    resources = set(sockets)
    if database is not None:
        resources.update((str(database), str(database) + ".lock"))
    servers = {process.pid: process for process in processes if process.command == "serve"}
    selected: dict[int, Process] = {}
    for process in processes:
        if process.handles & resources:
            # 行情源端口冲突时先关闭所属后端，避免其监控器立即重启行情源。
            owner = servers.get(process.parent, process)
            selected[owner.pid] = owner
    return list(selected.values())


def stop_processes(processes: list[Process], timeout: float = 15) -> None:
    descriptors = []
    try:
        # 先固定进程身份，避免检查和发信号之间 PID 被复用。
        for process in processes:
            try:
                descriptor = os.pidfd_open(process.pid)
            except ProcessLookupError:
                continue
            try:
                if process_state(process.pid)[1] != process.started:
                    os.close(descriptor)
                    continue
            except FileNotFoundError:
                os.close(descriptor)
                continue
            descriptors.append(descriptor)
        for descriptor in descriptors:
            with suppress(ProcessLookupError):
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        poller = select.poll()
        for descriptor in descriptors:
            poller.register(descriptor, select.POLLIN)
        deadline = time.monotonic() + timeout
        remaining = set(descriptors)
        while remaining:
            milliseconds = max(0, int((deadline - time.monotonic()) * 1000))
            if milliseconds == 0:
                raise RuntimeError("旧服务未在 15 秒内正常退出，已取消启动，请检查旧终端日志。")
            for descriptor, _event in poller.poll(milliseconds):
                remaining.discard(descriptor)
                poller.unregister(descriptor)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def ensure_ports_available(endpoints: list[tuple[str, int]]) -> None:
    for host, port in endpoints:
        family, socktype, proto, _, address = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )[0]
        with socket.socket(family, socktype, proto) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(address)
            except OSError as exc:
                raise RuntimeError(f"端口 {host}:{port} 无法使用：{exc}。请检查占用程序。") from exc


def prepare(config: Path) -> None:
    settings = load_settings(config)
    endpoints = [(settings.server.host, settings.server.port)]
    if settings.market is not None and settings.market.managed_sources:
        endpoints.extend(("127.0.0.1", source.port) for source in settings.market.sources)
    processes = project_processes()
    sockets = listening_sockets({port for _, port in endpoints})
    known_sockets = set().union(*(process.handles for process in processes))
    if sockets - known_sockets:
        raise RuntimeError("所需端口被其他程序占用，已取消启动；不会终止其他项目的进程。")
    database = settings.ledger.database if settings.ledger is not None else None
    conflicts = conflicting_processes(processes, sockets, database)
    if conflicts:
        print(
            "停止本项目的冲突服务（保留数据库）："
            + ", ".join(str(process.pid) for process in conflicts),
            flush=True,
        )
        stop_processes(conflicts)
        # 正常退出已包含行情清理；也处理父进程刚退出、遗留子进程尚未关闭的情况。
        parents = {process.pid for process in conflicts}
        children = [process for process in processes if process.parent in parents]
        stop_processes(children)
    ensure_ports_available(endpoints)
    print(
        f"启动工作台：http://{settings.server.host}:{settings.server.port}（Ctrl+C 停止）",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.config)
    except (ConfigurationError, RuntimeError, OSError) as exc:
        parser.exit(1, f"启动失败：{exc}\n")
    # 替换自身，让终端 Ctrl+C 和 SIGTERM 直接到达后端，沿用原有退出清理。
    os.execv(
        sys.executable, [sys.executable, "-m", "cta_risk", "serve", "--config", str(args.config)]
    )


if __name__ == "__main__":
    main()

"""源码与可执行发布共用的命令入口。"""

import argparse
from pathlib import Path

import uvicorn

from cta_risk import __version__
from cta_risk.bootstrap import create_app
from cta_risk.config.loader import ConfigurationError, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="cta-risk", description="期货交易风控与清算系统")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("serve", "启动 HTTP 服务"), ("check-config", "检查配置")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, required=True, help="YAML 配置路径")
    source_command = commands.add_parser("market-source", help="运行独立只读模拟行情源")
    source_command.add_argument("--config", type=Path, required=True)
    source_command.add_argument("--source-id", required=True)
    source_command.add_argument("--epoch-ms", type=int, required=True)
    source_command.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigurationError as exc:
        parser.error(str(exc))
    if args.command == "check-config":
        print(f"配置有效：{args.config.resolve()}")
        return
    if args.command == "market-source":
        from cta_risk.simulator.server import create_source_app

        if settings.market is None or settings.ledger is None:
            parser.error("配置未启用行情")
        source = next(
            (item for item in settings.market.sources if item.source_id == args.source_id), None
        )
        if source is None or args.epoch_ms < 0:
            parser.error("行情源标识或起点非法")
        uvicorn.run(
            create_source_app(settings, args.source_id, args.epoch_ms, parent_pid=args.parent_pid),
            host="127.0.0.1",
            port=source.port,
            workers=1,
        )
        return
    uvicorn.run(
        create_app(settings), host=settings.server.host, port=settings.server.port, workers=1
    )

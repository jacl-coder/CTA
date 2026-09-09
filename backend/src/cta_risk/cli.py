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
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigurationError as exc:
        parser.error(str(exc))
    if args.command == "check-config":
        print(f"配置有效：{args.config.resolve()}")
        return
    uvicorn.run(
        create_app(settings), host=settings.server.host, port=settings.server.port, workers=1
    )

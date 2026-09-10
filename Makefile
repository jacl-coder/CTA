.DEFAULT_GOAL := help
CONFIG ?= configs/live-demo.yaml

.PHONY: help install run run-market run-settlement run-workspace \
	demo-risk demo-market demo-settlement demo-live api check test build package verify

help:
	@echo "install  安装依赖"
	@echo "run      启动持续双源行情与交易工作台（8004，可指定 CONFIG）"
	@echo "run-market     启动固定断线补数展示（8002，约16秒）"
	@echo "run-settlement 启动两日自动清算展示（8003，约24秒）"
	@echo "run-workspace  启动手工交易与日结展示（8004）"
	@echo "run / run-* 自动正常停止本项目中端口或数据库冲突的旧服务，保留历史数据"
	@echo "页面展示需先启动再打开网址，Ctrl+C 停止；重启恢复历史进度"
	@echo "demo-risk       自动演示熔断、反弹保持和重启恢复"
	@echo "demo-market     自动演示双源中断、补数和进程恢复"
	@echo "demo-settlement 自动演示两日日结、平昨和日报恢复"
	@echo "demo-live       自动演示持续行情、跨日交易和重启恢复"
	@echo "demo-* 使用临时数据库和空闲端口，输出验证结果后退出，可重复运行"
	@echo "api      更新接口文档和前端类型"
	@echo "check    静态检查与类型检查"
	@echo "test     运行自动化测试"
	@echo "build    构建前端并同步到后端"
	@echo "package  构建程序与完整交付压缩包"
	@echo "verify   解压交付包并验证运行与恢复"

install:
	uv sync --project backend --all-groups --locked
	npm --prefix frontend ci --no-audit --no-fund

run: build
	uv run --project backend --locked python scripts/run_server.py --config "$(CONFIG)"

run-market:
	$(MAKE) run CONFIG=configs/market-demo.yaml

run-settlement:
	$(MAKE) run CONFIG=configs/settlement-demo.yaml

run-workspace:
	$(MAKE) run CONFIG=configs/workspace-demo.yaml

demo-risk:
	uv run --project backend --locked python scripts/demo_risk.py

demo-market:
	uv run --project backend --locked python scripts/demo_market.py

demo-settlement:
	uv run --project backend --locked python scripts/demo_settlement.py

demo-live:
	uv run --project backend --locked python scripts/demo_live.py

api:
	uv run --project backend --locked python scripts/export_openapi.py
	npm --prefix frontend run api:generate

check:
	uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts packaging/entrypoint.py
	uv run --project backend --locked ruff format --check --config backend/pyproject.toml backend scripts packaging/entrypoint.py
	cd backend && uv run --locked mypy
	npm --prefix frontend run typecheck
	npm --prefix frontend run lint

test:
	uv run --project backend --locked pytest backend/tests
	npm --prefix frontend test

build:
	npm --prefix frontend run build
	uv run --project backend --locked python scripts/sync_frontend.py

package: build
	uv run --project backend --locked --group packaging pyinstaller --noconfirm packaging/cta-risk.spec
	uv run --project backend --locked python scripts/delivery.py assemble

verify:
	uv run --project backend --locked python scripts/delivery.py verify

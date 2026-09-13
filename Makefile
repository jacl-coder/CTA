.DEFAULT_GOAL := help
CONFIG ?= configs/presentation.yaml

.PHONY: help install run new-demo run-sources \
	demo-risk demo-market demo-settlement demo-live api check test build package verify

help:
	@echo "install  安装依赖"
	@echo "run      启动手工演示工作台（8004）"
	@echo "new-demo 创建新的演示数据并启动工作台（8004）"
	@echo "run-sources 启动双源中断恢复展示（8003）"
	@echo "test     运行自动化测试"
	@echo "package  构建交付压缩包"
	@echo "verify   运行完整验收"
	@echo "run 会自动停止本项目中冲突的旧服务并保留运行数据"
	@echo "其他开发与验收命令见 README.md"

install:
	uv sync --project backend --all-groups --locked
	npm --prefix frontend ci --no-audit --no-fund

run: build
	uv run --project backend --locked python scripts/run_server.py --config "$(CONFIG)"

new-demo: build
	uv run --project backend --locked python scripts/new_presentation.py

run-sources: build
	uv run --project backend --locked python scripts/new_presentation.py --sources

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

.DEFAULT_GOAL := help
CONFIG ?= configs/live-demo.yaml

.PHONY: help install run api check test build package verify

help:
	@echo "install  安装依赖"
	@echo "run      启动持续双源行情与交易工作台（8004，可指定 CONFIG）"
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
	uv run --project backend --locked cta-risk serve --config "$(CONFIG)"

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

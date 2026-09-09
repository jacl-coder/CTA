.PHONY: help install api dev-backend dev-frontend check test build-web package smoke-package

help:
	@echo "install       安装锁定的后端和前端依赖"
	@echo "api           导出 OpenAPI 并生成前端类型"
	@echo "dev-backend   启动后端（8000）"
	@echo "dev-frontend  启动 Vite（5173，另一个终端）"
	@echo "check         静态检查和类型检查"
	@echo "test          后端与前端测试"
	@echo "build-web     构建并同步页面到后端"
	@echo "package       构建 PyInstaller 可执行目录"
	@echo "smoke-package 检查已构建发布目录的基础启动"

install:
	uv sync --project backend --all-groups --locked
	npm --prefix frontend ci --no-audit --no-fund

api:
	uv run --project backend --locked python scripts/export_openapi.py
	npm --prefix frontend run api:generate

dev-backend:
	uv run --project backend --locked cta-risk serve --config configs/demo.yaml

dev-frontend:
	npm --prefix frontend run dev

check:
	uv run --project backend --locked ruff check --config backend/pyproject.toml backend scripts packaging/entrypoint.py
	uv run --project backend --locked ruff format --check --config backend/pyproject.toml backend scripts packaging/entrypoint.py
	cd backend && uv run --locked mypy
	npm --prefix frontend run typecheck
	npm --prefix frontend run lint

test:
	uv run --project backend --locked pytest backend/tests
	npm --prefix frontend test

build-web:
	npm --prefix frontend run build
	uv run --project backend --locked python scripts/sync_frontend.py

package: build-web
	uv run --project backend --locked --group packaging pyinstaller --noconfirm packaging/cta-risk.spec

smoke-package:
	uv run --project backend --locked python scripts/smoke_package.py

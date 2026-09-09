# 多品种期货交易风控与清算系统

上海宽投资产管理有限公司机试项目。后端采用 Python 3.12、FastAPI，前端采用 React、TypeScript、Vite 和 Ant Design。

当前已实现核算领域函数、SQLite 账本持久化、账户初始化、单写者应用服务，以及账户、持仓、成交记录和多账户持仓汇总的只读 API。核算结果以独立手算样例验证，事务与重启恢复已有测试。行情、下单风控、自动清算和日报尚未接入；React 页面展示真实服务状态，当前不提供交易操作。

## 项目依据

机试方实际只提供《机试题目-多品种期货交易风控系统.md》，它是唯一的外部需求与验收依据。其余文档均为本项目内部编写的需求整理、设计和验证记录；自主设计应服务于原题，并可根据实现效果调整。

- [机试原题](docs/机试题目-多品种期货交易风控系统.md)
- [需求基线](docs/需求基线.md)
- [技术选型与项目结构](docs/技术选型与项目结构.md)
- [目录与开发约定](docs/项目结构与开发约定.md)
- [工程骨架验证记录](docs/工程骨架验证.md)
- [业务口径与计算样例](docs/业务口径与计算样例.md)
- [账本接口与持久化说明](docs/账本接口与持久化.md)
- [测试方案与需求进度](docs/测试方案.md)

## 本地开发

需要 Python 3.12、uv、Node.js 22.12 或更高版本及 npm。在仓库根目录运行：

```bash
make install
make dev-backend
```

另开终端，在仓库根目录启动页面：

```bash
make dev-frontend
```

访问 [工作台](http://127.0.0.1:5173) 或 [API 文档](http://127.0.0.1:8000/api/docs)。Vite 将 `/api` 请求代理至后端。当前页面读取真实后端状态，业务模块尚未接入时显示“交易服务尚未就绪”。

不使用 Make 时，对应命令如下：

```bash
uv sync --project backend --all-groups --locked
npm --prefix frontend ci
uv run --project backend --locked cta-risk serve --config configs/demo.yaml
# 另开终端
npm --prefix frontend run dev
```

配置通过 `--config` 显式指定，资源路径相对配置文件解析。`configs/demo.yaml` 包含服务参数、3 个账户、3 个合约及 4 条标注 `bootstrap-demo` 来源的初始化成交。首次启动创建 `var/demo/ledger.sqlite3`，后续启动恢复已有账本，不重复导入。金额、价格和交易日必须用带引号的字符串填写。校验命令只检查配置，不创建数据库：

```bash
uv run --project backend --locked cta-risk check-config --config configs/demo.yaml
```

同一数据库的运行标识、交易日与业务配置需要保持一致；修改初始资金、合约规则或初始化成交时，指定新的数据库路径和运行标识。当前持久化支持单运行、单交易日，跨日日结将在后续阶段接入。

## 验证与接口契约

```bash
make check
make test
make api
```

`make api` 导出 [OpenAPI](docs/openapi.json)，再生成前端 `schema.d.ts`。后端接口变化时应一起更新这两个文件。依赖版本分别由 `backend/uv.lock` 与 `frontend/package-lock.json` 锁定。

- `/api/health/live`：HTTP 进程存活时返回 200。
- `/api/health/ready`：当前返回 503，账本可查询并不表示可以交易。
- `/api/system`：返回版本、建设阶段、账本可用性、可交易状态和阻止原因。
- `/api/accounts`、`/api/accounts/{account_id}`：查询账户及当日已实现盈亏、费用。
- `/api/positions?account_id=A&product_id=SHFE.rb`：按账户及品种查询持仓。
- `/api/positions/summary?product_id=SHFE.rb`：跨账户汇总同合约同方向手数。
- `/api/trades?account_id=A`：查询成交及来源，支持品种筛选和分页。
- 未实现的 `/api/*` 和缺失静态资源返回 404，不回退成成功页面。

## 前端构建与可执行目录

```bash
make build-web
make dev-backend
```

构建后直接访问 [后端托管的工作台](http://127.0.0.1:8000)，无需运行 Vite。

```bash
make package
make smoke-package
./dist/cta-risk/cta-risk serve --config configs/demo.yaml
```

PyInstaller 采用 `onedir`，必须分发整个 `dist/cta-risk` 目录及需要的配置，不能只复制其中的启动文件。当前打包范围包括后端、React 页面与 SQL 迁移文件。`make smoke-package` 在临时目录、无 Python/Node 搜索路径的环境下验证初始化、查询及强制退出后的恢复；两个模拟源在实现后纳入打包验收。

首个构建目标为 Linux amd64，发布包需在兼容操作系统运行。源码启动、打包启动与最终完整机试验收是三个不同的完成条件。

## 下一阶段

下一阶段在现有应用层接入风控规则与模拟执行，验证账户浮亏达到配置阈值后实际阻止继续开仓。资金与持仓检查、成交核算、风险状态持久化共同构成交易闭环；双源恢复和日结调度随后接入，具体顺序见技术方案第 9 节。

只运行核算基础测试：

```bash
uv run --project backend --locked pytest backend/tests/unit/test_accounting.py -v
```

当前核算测试 26 项，后端合计 62 项，前端 2 项。测试中的需求编号表示已覆盖的行为，不等于完整系统验收完成。

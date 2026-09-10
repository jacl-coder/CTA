# 多品种期货交易风控与清算系统

上海宽投资产管理有限公司机试项目。后端采用 Python 3.12、FastAPI，前端采用 React、TypeScript、Vite 和 Ant Design。

当前已实现核算、SQLite 账本和模拟交易风控闭环：价格更新触发资金/持仓规则，下单前检查风险、预计敞口和保证金，熔断后实际拒绝继续开仓。订单结果、成交与风险状态共同持久化，重启恢复后保留熔断和幂等结果。两个独立 HTTP 行情源已接通，支持中断补数、按序风控、去重和进程恢复。已接通逻辑收盘调度、全账户事务日结、跨日持仓和 HTML/JSON 风控日报，支持两日联合演示。React 已接入账户总览、持仓成交、行情监控、风险事件、模拟交易及日结日报，可在页面操作并核对已有核心流程。

## 项目依据

机试方实际只提供《机试题目-多品种期货交易风控系统.md》，它是唯一的外部需求与验收依据。其余文档均为本项目内部编写的需求整理、设计和验证记录；自主设计应服务于原题，并可根据实现效果调整。

- [机试原题](docs/机试题目-多品种期货交易风控系统.md)
- [项目进度与后续计划](docs/项目进度.md)
- [交付与原题验收](docs/交付与原题验收.md)
- [现场演示与答辩](docs/现场演示与答辩.md)
- [代码展示](docs/代码展示.md)
- [需求基线](docs/需求基线.md)
- [技术选型与项目结构](docs/技术选型与项目结构.md)
- [目录与开发约定](docs/项目结构与开发约定.md)
- [工程骨架验证记录](docs/工程骨架验证.md)
- [业务口径与计算样例](docs/业务口径与计算样例.md)
- [账本接口与持久化说明](docs/账本接口与持久化.md)
- [交易与风控闭环说明](docs/交易与风控闭环.md)
- [双源行情与恢复说明](docs/双源行情与恢复.md)
- [日结与风控日报](docs/日结与风控日报.md)
- [前端工作台接入与页面演示](docs/前端工作台接入.md)
- [测试方案与需求进度](docs/测试方案.md)

## 启动项目

需要 Python 3.12、uv、Node.js 22.12 或更高版本及 npm。在仓库根目录运行：

```bash
make install  # 首次安装依赖
make run
```

访问 [工作台](http://127.0.0.1:8004) 或 [API 文档](http://127.0.0.1:8004/api/docs)。`make run` 自动构建页面并启动后端，默认使用 `configs/live-demo.yaml`，自动启动两个独立模拟行情源（8301/8302）。每秒更新完整行情，连接就绪后即可在“模拟交易”开平仓，不需要手工输入价格。每 30 分钟完成一个模拟交易日，自动清算、导出日报并进入下一日，行情不会在固定帧数后结束。停止服务按 `Ctrl+C`。操作步骤见[前端工作台接入](docs/前端工作台接入.md)。

交易仍受账户熔断、敞口和保证金规则约束；收盘或断线补数期间会暂停，补齐并进入可交易状态后恢复。模拟日期按连续日期推进，合约和结算价使用演示模板，不代表真实交易所日历或结算规则。重启保留成交、报告和行情进度，需先补齐停机期间的数据。

需要逐步手工演示时，使用 `make run CONFIG=configs/workspace-demo.yaml`。切换固定双源日结验收场景：

```bash
make run CONFIG=configs/settlement-demo.yaml
```

该场景访问 8003，行情源使用 8201/8202。不同场景的端口、数据库和运行标识由配置决定，重启会恢复已有数据。

修改前端需要热更新时，保持后端运行，另开终端：

```bash
CTA_API_TARGET=http://127.0.0.1:8004 npm --prefix frontend run dev
```

访问 5173。Vite 将 `/api` 代理到指定后端，默认代理为 8000。只修改后端、不需要重新构建页面时，可以直接使用 `uv run --project backend --locked cta-risk serve --config configs/live-demo.yaml`。

配置路径通过 `--config` 指定，数据库和报告目录相对配置文件解析。金额、价格和交易日使用带引号的字符串。修改已有运行的初始资金、规则或初始化成交时，使用新的数据库路径与运行标识；同一运行跨日时保持配置首日不变。只检查配置而不创建数据库：

```bash
uv run --project backend --locked cta-risk check-config --config configs/live-demo.yaml
```

Makefile 保留 `install`、`run`、`api`、`check`、`test`、`build`、`package`、`verify` 八个入口，执行 `make` 查看说明。专项演示使用下方脚本。

## 模拟交易与熔断演示

```bash
uv run --project backend --locked python scripts/demo_risk.py
```

脚本在独立临时数据库中启动真实 HTTP 服务，依次验证浮亏 2980 元、达到 3000 元熔断、拒绝开仓、反弹保持熔断、允许平仓、多账户独立运行及强制退出后的恢复，完成后退出并清理临时数据。

手动演示使用 `make run CONFIG=configs/trading-demo.yaml`，加载 `configs/trading-demo.yaml`，访问 [8001 端口的 API 文档](http://127.0.0.1:8001/api/docs)。先通过 `/api/session` 取得本次运行令牌，再调用 `/api/simulation/frames` 提交完整模拟价格，最后通过 `/api/orders` 下单。价格有效期默认 30 秒，恢复或过期后需要新帧。具体字段、规则和操作步骤见[闭环说明](docs/交易与风控闭环.md)。

此配置与默认账本模式使用不同的运行、数据库和端口；Vite 默认代理仍指向 8000。3% 为可配置演示阈值，两个独立行情源使用下面的自动行情配置。

## 双源行情与恢复演示

```bash
uv run --project backend --locked python scripts/demo_market.py
# 手动运行固定场景：后端 8002，行情源 8101/8102
make run CONFIG=configs/market-demo.yaml
```

`uv run --project backend --locked python scripts/demo_market.py` 使用临时数据库和空闲端口，实际启动后端与两个行情服务，对比连续接入和中断补全结果。覆盖中断时暂停开平仓、后端强制结束后恢复、行情源单独退出后自动重启，以及中断期间越过阈值又反弹后的正确熔断。

`make run CONFIG=configs/market-demo.yaml` 读取 `configs/market-demo.yaml`，访问 [双源 API 文档](http://127.0.0.1:8002/api/docs)。场景共 64 帧、约 16 秒，结束后保留结果并停止交易；重启沿用数据库中的旧进度。重复演示优先用 `uv run --project backend --locked python scripts/demo_market.py`，手动运行新场景需要新的数据库和运行标识。双源模式禁用手工价格注入，详细协议、接口与限制见[双源行情与恢复](docs/双源行情与恢复.md)。

## 两日日结与日报

```bash
uv run --project backend --locked python scripts/demo_settlement.py
# 固定两日场景：后端 8003，行情源 8201/8202
make run CONFIG=configs/settlement-demo.yaml
```

验收脚本使用临时数据库验证自动封账、独立结算价清算、次日平昨、重复日结、跨日成交后的强制重启，以及日报文件与冻结快照一致。固定场景约 24 秒，生成 `var/settlement-demo/reports/risk-YYYY-MM-DD.html` 和 `.json`，可以离线打开。固定场景本身不自动下单；平昨订单由验收脚本提交。

访问 [日结 API 文档](http://127.0.0.1:8003/api/docs)，查看阶段、日报或手工触发收盘与清算。具体规则、请求和独立手算见[日结与风控日报](docs/日结与风控日报.md)。

## 验证与接口契约

```bash
make check
make test
make api
```

`make api` 导出 [OpenAPI](docs/openapi.json)，再生成前端 `schema.d.ts`。后端接口变化时应一起更新这两个文件。依赖版本分别由 `backend/uv.lock` 与 `frontend/package-lock.json` 锁定。

- `/api/health/live`：HTTP 进程存活时返回 200。
- `/api/health/ready`：账本模式或行情无效时返回 503；交易模式有有效行情时返回 200，具体订单仍需经过账户风控。
- `/api/workspace`：同一次读取的工作台快照、能力、全账户金额汇总与冻结日结计划。
- `/api/system`：返回版本、建设阶段、账本可用性、可交易状态和阻止原因。
- `/api/accounts`、`/api/accounts/{account_id}`：查询账户及当日已实现盈亏、费用。
- `/api/positions?account_id=A&product_id=SHFE.rb`：按账户及品种查询持仓。
- `/api/positions/summary?product_id=SHFE.rb`：跨账户汇总同合约同方向手数。
- `/api/trades?account_id=A`：查询成交及来源，支持品种筛选和分页。
- `/api/risk`、`/api/risk-events`、`/api/market`、`/api/orders`：查询交易运行的风险、价格和订单结果。
- `/api/market/sources`：双源模式的连接、连续接收和应用进度；风险事件另带发生时间、检测时间和恢复发现标记。
- `/api/settlement/status`、`/api/reports`：当前日结阶段、已结算日期、文件错误和冻结日报。
- 未实现的 `/api/*` 和缺失静态资源返回 404，不回退成成功页面。

## 完整交付包

```bash
make package
make verify
```

`make package` 构建程序并生成 `dist/cta-risk-delivery-linux-x86_64.tar.gz` 和 `.sha256`，包含源码、测试、配置、文档、代码展示图及 `dist/cta-risk/` 可执行目录。`make verify` 解压这个归档，核对文件摘要和可执行权限，再用解压后的程序验证账本恢复、实际熔断拒单、双源恢复和两日日结。日志和归档摘要绑定记录位于 `dist/delivery-verification/`。

评审解压后，在 `cta-risk-delivery` 目录执行：

```bash
./dist/cta-risk/cta-risk serve --config configs/live-demo.yaml
```

访问 8004，无需 Python、Node 或前端开发服务。源代码修改与自动化验证脚本仍需要开发依赖。可执行程序必须与 `_internal` 等运行文件一起分发。

首个构建与验证环境为 Ubuntu 24.04.4、Linux x86_64、glibc 2.39。其他目标环境需独立验证。交付清单、校验和使用方法见[交付与原题验收](docs/交付与原题验收.md)。

## 下一阶段

前端业务工作台、实时指标和多账户对比已完成本阶段接入。本次补齐完整交付包、原题验收清单、现场演示与答辩、代码展示材料。剩余独立历史回放附加项与机试方实际目标环境验证，具体顺序见项目进度文档。

只运行核算基础测试：

```bash
uv run --project backend --locked pytest backend/tests/unit/test_accounting.py -v
```

当前核算测试 26 项，后端合计 189 项，前端 18 项。测试中的需求编号表示已覆盖的行为，不等于完整系统验收完成。

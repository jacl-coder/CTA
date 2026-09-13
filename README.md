# 多品种期货交易风控与清算系统

上海宽投资产管理有限公司机试项目。后端采用 Python 3.12、FastAPI，前端采用 React、TypeScript、Vite 和 Ant Design。

项目实现了多账户、多品种期货的持仓、盘中风控、日结和风控日报。默认工作台用手工模拟行情演示完整交易闭环；固定双源场景用来验证中断补数和风险恢复。所有价格、账户和成交均是本地模拟数据。

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
- [业务口径与计算样例](docs/业务口径与计算样例.md)
- [账本接口与持久化说明](docs/账本接口与持久化.md)
- [交易与风控闭环说明](docs/交易与风控闭环.md)
- [双源行情与恢复说明](docs/双源行情与恢复.md)
- [日结与风控日报](docs/日结与风控日报.md)
- [前端工作台接入与页面演示](docs/前端工作台接入.md)
- [测试方案与需求进度](docs/测试方案.md)

## 启动与演示

需要 Python 3.12、uv、Node.js 22.12 或更高版本及 npm。首次运行只需：

```bash
make install
make run
```

打开 [工作台](http://127.0.0.1:8004)。`make run` 自动构建页面、关闭本项目冲突的旧服务，并使用内置的 `configs/presentation.yaml` 启动手工演示工作台。它保留已有运行数据；停止服务按 `Ctrl+C`。

需要从头展示时，执行 `make new-demo`。它会自动创建一份新的账本和日报目录并启动相同的工作台，旧演示记录不会被删除。

页面按以下顺序操作即可完成核心展示：

1. 在“账户总览”说明三个账户、三个合约与风险状态。
2. 打开“模拟交易”，先输入完整行情：螺纹钢 3500、铁矿石 800、IF 4000；此时才允许交易。
3. 为账户 A 的螺纹钢开多 1 手，再将螺纹钢改为 3400、其余价格不变，展示 3% 浮亏熔断和拒绝继续开仓。
4. 将螺纹钢恢复到 3500，在“风险事件”展示熔断仍保留及实际发生时间。
5. 在“日结与日报”依次封账、清算、结转，展示冻结日报和次日昨仓。

页面中的“模拟交易日”是可重复验收的业务日期；订单和风险记录的“实际处理时间（北京时间）”取自电脑时钟。具体计算口径与现场讲解见[现场演示与答辩](docs/现场演示与答辩.md)。

需要单独展示题目要求的两个独立行情源及中断补数时，另开终端执行：

```bash
make run-sources
```

打开 [双源展示工作台](http://127.0.0.1:8003)。该命令会创建独立的双源演示数据，固定场景自动推进两个模拟交易日，约 24 秒完成；“行情监控”可查看两个源的连接、补数和处理进度。双源场景与手工演示使用独立数据，互不影响。

## 开发与验收

日常开发常用命令：

```bash
make check      # 静态检查与类型检查
make test       # 自动化测试
make package    # 生成交付压缩包
make verify     # 解压交付包并运行完整验收
```

高级验收脚本和其他配置保留在项目中，用于自动核对熔断、双源恢复、两日日结和重启恢复，不是现场操作的前置条件。配置参数、双源协议和交付细节分别见[交易与风控闭环](docs/交易与风控闭环.md)、[双源行情与恢复](docs/双源行情与恢复.md)、[日结与风控日报](docs/日结与风控日报.md)和[交付与原题验收](docs/交付与原题验收.md)。

<details>
<summary>配置用途与开发命令</summary>

`configs/` 只保留四份有独立用途的模板：

- `presentation.yaml`：手工报价、交易、熔断和日结展示。
- `settlement-demo.yaml`：双源故障与两日清算展示。
- `live-demo.yaml`：持续双源行情，每 30 分钟一个模拟交易日；使用 `make run CONFIG=configs/live-demo.yaml` 启动。
- `market-demo.yaml`：不含日结的双源补数验收，由 `make demo-market` 使用。

账本和单独熔断测试从手工模板裁取所需配置，不再维护重复文件。`make api` 更新接口契约；`make demo-risk`、`make demo-market`、`make demo-settlement`、`make demo-live` 分别运行专项验收。

</details>

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

`make package` 构建程序并生成 `dist/cta-risk-delivery-linux-x86_64.tar.gz` 和 `.sha256`，包含源码、测试、配置、文档、代码展示图及 `dist/cta-risk/` 可执行目录。`make verify` 解压这个归档，核对文件摘要和可执行权限，再用解压后的程序验证账本恢复、实际熔断拒单、双源恢复、两日日结，以及持续行情工作台的跨日交易与重启。日志和归档摘要绑定记录位于 `dist/delivery-verification/`。

评审解压后，在 `cta-risk-delivery` 目录执行：

```bash
./dist/cta-risk/cta-risk serve --config configs/presentation.yaml
```

访问 8004，无需 Python、Node 或前端开发服务。源代码修改与自动化验证脚本仍需要开发依赖。可执行程序必须与 `_internal` 等运行文件一起分发。

首个构建与验证环境为 Ubuntu 24.04.4、Linux x86_64、glibc 2.39。其他目标环境需独立验证。交付清单、校验和使用方法见[交付与原题验收](docs/交付与原题验收.md)。

后端和前端测试的范围与原题映射见[测试方案](docs/测试方案.md)。

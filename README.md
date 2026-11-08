# SafeOrbit 安轨

面向 Agent 运行期的独立审计与治理层：采集轨迹、规则审计、语义裁判、策略拦截、人工审批与处置闭环。

## 能力概览

- **采集**：OpenAI 兼容网关、OTLP/HTTP JSON 与 protobuf、Python SDK、MCP stdio 代理、MCP over HTTP 代理、LangChain / LangGraph / LlamaIndex 回调。
- **审计**：归一化、成本核算、PII 检测、内容安全 Guard（提示注入/数据外泄/输出泄露/RAG 注入）、内置规则、Agent 历史基线偏离、LLM 语义裁判、单轨迹 HTML/JSON 报告、跨轨迹汇总报告。
- **治理**：Agent Key 与 Scope、工具级权限校验、按 Agent 策略、策略试算、写操作一次性审批票、处置提案-审批-执行、会话隔离、工具阻断、规则回检。
- **可信**：每份报告保留版本、内容哈希与 `prev_hash` 链，支持一键校验；可配置入库前脱敏；支持证据包导出与保留期清理。
- **接入管理**：本地账号与四级角色、可信反向代理 SSO、租户隔离、模型目录与密钥保护。
- **代码与供应链**：正则 + Python AST、依赖 CVE 扫描、CycloneDX SBOM、Dockerfile / K8s 配置检查、`.safeorbitignore`、基线增量扫描、JSON/HTML/SARIF 报告。
- **评测**：内置红队提示集、检出率统计、轨迹工具/成本/判定评测。

## 快速开始

```powershell
py -m pip install -e .
py -m safeorbit user init --username admin
py -m safeorbit serve --mock-upstream --port 8000
```

打开 <http://127.0.0.1:8000>，使用刚创建的账号登录。

离线审计一份轨迹：

```powershell
py -m safeorbit audit fixtures/demo_unsafe_refund.json --format both
```

代码审计：

```powershell
py -m safeorbit code-audit .\safeorbit --format all
py -m safeorbit code-audit .\src --baseline reports/codeaudit/baseline.json --format sarif
```

依赖与基础设施审计：

```powershell
py -m safeorbit sca .\src --format both
py -m safeorbit sca .\src --advisories .\advisories.json --fail-on high
py -m safeorbit infra-scan .\deploy --format both
```

红队与证据留存：

```powershell
py -m safeorbit eval --redteam
py -m safeorbit evidence export --out reports/evidence/evidence.zip
py -m safeorbit retention --trace-days 90              # 预演
py -m safeorbit retention --trace-days 90 --apply      # 真正清理
```

备份与恢复（SQLite 在线备份，服务运行中也可执行）：

```powershell
py -m safeorbit backup                 # 生成 data/safeorbit.db.backup-<时间戳>
py -m safeorbit backup --out D:\backup\safeorbit.db
py -m safeorbit backup --list
py -m safeorbit restore D:\backup\safeorbit.db --confirm
```

## 评审对齐

| 评分维度 | 项目对应证据 | 现场怎么看 |
| --- | --- | --- |
| 创意与场景价值（30%） | 三类真实事故：越权退款、提示注入、RAG 投毒；每条都能复现并给出定位到步骤的判定 | `py -m safeorbit demo` 后打开对应轨迹 |
| AI 应用深度（30%） | 规则打底 + 语义裁判（温度 0、强制 JSON、定位校验、失败降级、熔断）；红队提示集可量化检出率 | 控制台「模型设置 → 独立裁判提示词（只读）」；`py -m safeorbit eval --redteam` |
| 完成度与可落地性（25%） | 395 项回归测试、CI、Docker 一键部署、审批/处置/证据导出闭环 | `docker compose up -d --build`；`py -m pytest -q` |
| 展示表达与推广潜力（15%） | 初始页给出通俗说明与三分钟演示路线；接入方式按改造成本排序 | 打开首页 → 三分钟演示 |

三分钟演示：

```powershell
# 1. 控制台「智能体接入」注册 Agent（例如 ecs-cs-agent）并签发 Key
$env:SAFEORBIT_AGENT_KEY = '<一次性 Agent Key>'

# 2. 注入演示轨迹：越权退款 / 提示注入 / RAG 投毒 / 正常对照
#    属于其他 Agent 的轨迹会自动跳过，不影响演示
py -m safeorbit demo

# 3. 打开控制台，点开任一轨迹看五视图；到「审批队列」处理写操作
```

## 部署与运行

```powershell
docker compose up -d --build
```

- 首次启动用 `SAFEORBIT_ADMIN_USERNAME` / `SAFEORBIT_ADMIN_PASSWORD` 初始化管理员，已存在则跳过（幂等）。
- 容器以非 root 运行，`/data` 存 SQLite，`/reports` 存报告与证据包，均为卷。
- 探针：`/health/live`（进程存活）、`/health/ready`（数据库可用性，异常返回 503）、`/metrics`（Prometheus 文本）。
- 管理员可在控制台「智能体接入 → 运行状态」查看依赖健康、熔断状态与进程内计数。

## Agent 接入

### 1. 网关模式（最少改动）

把模型 `base_url` 指向 `http://127.0.0.1:8000/v1`，使用 Agent Key：

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key=os.environ["SAFEORBIT_AGENT_KEY"])
```

### 2. Python SDK

```python
from safeorbit.adapters import SafeOrbitClient, traced_tool

client = SafeOrbitClient("http://127.0.0.1:8000", agent_id="my-agent")
with traced_tool(client, "refund", {"order_id": "O-1", "amount": 100}, side_effect="write"):
    ...  # 真实工具调用
```

写操作在 enforce 模式下需要审批票，可通过 `traced_tool(..., approval_id="apr-...")` 传入。

### 3. OTLP

- JSON：`POST /v1/traces`，`Content-Type: application/json`
- Protobuf：`POST /v1/traces`，`Content-Type: application/x-protobuf`
- resource 中的 `service.name` 必须与 Agent Key 对应

### 4. MCP

```powershell
# stdio
py -m safeorbit mcp-proxy --url http://127.0.0.1:8000 --agent-id mcp-agent -- npx -y @modelcontextprotocol/server-filesystem .

# HTTP
py -m safeorbit mcp-http-proxy --upstream http://127.0.0.1:3000/mcp --url http://127.0.0.1:8000 --agent-id mcp-agent
```

两者默认仅采集；加 `--enforce` 才做执行前检查，写操作审批通过后把审批单 ID 传给 `--approval-id`。

### 5. 其他框架

`py -m safeorbit probe --framework <name>` 可生成接入片段，支持：
`generic`、`openai`、`langchain`、`langgraph`、`llamaindex`、`autogen`、`crewai`、`otel`、`mcp`、`mcp-http`。

## 治理流程

1. **策略中心**：按 Agent 配置模式、预算、工具白名单、写金额阈值、启用规则与脱敏。保存前可“试算最近 100 条”，查看切 enforce 后的影响面。
2. **写操作审批**：enforce 下写工具会生成审批单，operator/admin 批准后得到 15 分钟有效、绑定参数哈希的一次性票据。
3. **处置中心**：自动/人工提案 → 审批 → 执行（隔离会话或阻断工具）→ 回检后续轨迹；支持告知 Webhook。
4. **轨迹审计**：筛选、分页、CSV 导出、时间轴回放、Span 原始内容、证据链校验、报告版本查看。

## 审计五视图

打开任意轨迹后可在五个视图间切换，分别回答一个监管问题：

| 视图 | 回答的问题 | 数据来源 |
| --- | --- | --- |
| 任务时间线 | 智能体按顺序做了什么？ | span 顺序、耗时、命中项 |
| 资源触碰 | 碰了哪些文件 / 接口 / 数据 / 业务对象？ | 工具参数推断资源，红写 / 黄执行 / 绿读 |
| 权限与授权 | 谁给了什么权限、用在了哪？ | `tool.permission`、审批票据、客户端自报标识 |
| 决策来源 | 为什么做这个决策？ | 前置提示 → 检索上下文 → 工具调用 → 结果 |
| 持久变更 | 任务结束后还剩什么影响？ | 全部写操作清单；安轨不持有回滚能力，需业务侧撤销 |

接口：`GET /api/traces/{trace_id}/audit-views`（viewer，按租户过滤）。

## 监管指标（HOTL）

审计页“监管指标 · HOTL”提供 5 个可点击指标，每个都对应一个动作：

- **升级率**（自动 → 人工）：风险记录占比，点击跳转风险处置
- **驳回率**：已决策审批中驳回比例，点击打开审批队列
- **漂移信号**：命中 `baseline_new_tool` / `baseline_cost_spike` 的记录占比，点击按“待复核”筛选
- **平均审批决策时长**：过短会提示橡皮图章风险，点击打开审批队列
- **近 7 天成本**：与上一周期对比，点击打开审计汇总

接口：`GET /api/audit/metrics`（viewer）。

## 人审（HITL）约束

- 审批卡片按工具名分级：删除/退款/转账等标为“极高风险”，写操作标为“高风险”，其余“需确认”。
- 极高/高风险操作必须填写审批理由才能批准，理由写入审批记录。
- 审批单显示参数指纹与关联轨迹入口（可跳到五视图追溯决策来源）。
- 审批卡片摘要显示平均决策时长，便于发现“秒批”行为。


## 品牌标识

| 文件 | 用途 |
| --- | --- |
| `safeorbit/assets/logo.svg` | 主标识（深色圆角底），同时作为站点 favicon |
| `safeorbit/assets/logo-mark.svg` | 透明底图形，用于深色界面或叠加场景 |
| `safeorbit/assets/logo-horizontal.svg` | 图形 + 字标 + 定位语，用于文档封面与对外材料 |

标识含义：

- **中心盾**：安全与治理的核心。盾体是“装甲分面”——左侧受光、右侧沉入阴影，中缝断开处留给对勾，形成立体切割感；盾外人眼可见的青色光晕表示它一直在工作。盾内负空间是一个对勾，表示“通过校验”——审计的目的不是展示风险，而是确认可以放行。
- **外轨（青绿→紫渐变，带缺口）**：采集层。缺口表示审计不是黑箱，链路可见、可解释。
- **中轨（虚线）**：审计层。虚线是逐条轨迹的离散采样，每个点都对应一次可复核的证据。
- **内轨（四段短弧）**：治理边界。断成短弧让盾牌嵌入其中，表示边界由规则构成，而不是一堵封闭的墙。
- **三个节点**：青绿节点是人工闸门（审批 / 阻断），紫色节点是交接边界（多智能体或多系统），琥珀节点是证据锚点（可定位、可校验）。
- **配色**：青绿 `#65D6C1` 表示可信与通过，紫色 `#B4A5E8` 表示需要复核的语义判断，琥珀 `#EDC279` 表示需要注意的中间态。

## 角色

`viewer < operator < auditor < admin`

- 查看轨迹/报告：viewer
- 审批写操作、执行处置、解除限制：operator
- 修改策略、生成汇总报告、代码审计：auditor
- 用户、Agent、Key、模型设置、操作日志：admin

## SSO（可信反向代理断言）

由 oauth2-proxy 等完成 IdP 认证，再向 `/api/auth/sso` 提交签名断言：

```json
{
  "username": "alice",
  "groups": ["safeorbit-auditor"],
  "timestamp": "2026-01-01T12:00:00+00:00",
  "signature": "HMAC-SHA256(secret, username + '\\n' + groups_csv + '\\n' + timestamp)"
}
```

相关环境变量：

- `SAFEORBIT_SSO_ENABLED=1`
- `SAFEORBIT_SSO_SECRET=<共享密钥>`
- `SAFEORBIT_SSO_ADMIN_GROUP` / `SAFEORBIT_SSO_AUDITOR_GROUP` / `SAFEORBIT_SSO_OPERATOR_GROUP`
- `SAFEORBIT_SSO_DEFAULT_ROLE=viewer`

## 多租户

- Agent、用户、轨迹均带 `tenant_id`；非管理员只能看到本租户轨迹与 Agent。
- 管理员默认跨租户可见；注册 Agent 时可显式指定 `tenant_id`。
- Agent ID 全局唯一，策略/处置仍按 Agent ID 归属。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `SAFEORBIT_DB` | SQLite 路径，默认 `data/safeorbit.db` |
| `SAFEORBIT_MODE` | `shadow`（默认）或 `enforce` |
| `SAFEORBIT_UPSTREAM_MOCK` | `1` 时使用离线假上游 |
| `SAFEORBIT_JUDGE` | 语义裁判开关 |
| `SAFEORBIT_JUDGE_PROFILE` | 裁判模型别名 |
| `SAFEORBIT_UPSTREAM_BASE_URL` | 旧版单上游地址 |
| `SAFEORBIT_WEBHOOK_URL` | 告警/审批/处置 Webhook（公网 HTTPS） |
| `SAFEORBIT_WEBHOOK_TOKEN` | Webhook Bearer Token |
| `SAFEORBIT_SCA_ADVISORIES` | 本地依赖漏洞库 JSON 路径 |
| `SAFEORBIT_TRACE_RETENTION_DAYS` | 轨迹保留天数，默认 90 |
| `SAFEORBIT_REPORT_VERSION_KEEP` | 每条轨迹保留的报告版本数，默认 10 |
| `SAFEORBIT_AUDIT_RETENTION_DAYS` | 操作日志保留天数，默认 365 |
| `SAFEORBIT_CORS_ORIGINS` | 允许的来源，逗号分隔 |
| `SAFEORBIT_MAX_REQUEST_BYTES` / `SAFEORBIT_MAX_SPANS` | 请求体与批次上限 |
| `SAFEORBIT_REQUESTS_PER_MINUTE` / `SAFEORBIT_LOGIN_PER_MINUTE` | 限流 |
| `SAFEORBIT_STREAM_AUDIT_MAX_BYTES` | 流式审计缓存上限 |
| `SAFEORBIT_LOG_FORMAT` | `text`（默认）或 `json` 结构化日志 |
| `SAFEORBIT_LOG_LEVEL` | 日志级别，默认 `INFO` |
| `SAFEORBIT_UPSTREAM_TIMEOUT` | 上游模型调用超时秒数，默认 120 |
| `SAFEORBIT_UPSTREAM_FAILURE_THRESHOLD` / `SAFEORBIT_UPSTREAM_RECOVERY_SECONDS` | 上游熔断阈值与恢复窗口 |
| `SAFEORBIT_JUDGE_FAILURE_THRESHOLD` | 裁判熔断阈值，默认连续 5 次失败 |
| `SAFEORBIT_COOKIE_SECURE` | 会话 Cookie 是否仅 HTTPS；容器本地调试可设 0 |
| `SAFEORBIT_ADMIN_USERNAME` / `SAFEORBIT_ADMIN_PASSWORD` | 容器首次启动的管理员初始化 |
| `SAFEORBIT_USER_PASSWORD` | 非交互式 `user init` 的密码来源（CI/容器） |

## 代码审计

- 支持 Python AST 补充检查，减少注释/字符串误报。
- 根目录 `.safeorbitignore` 可按规则 ID 或文件 glob 忽略，例如：

```
unsafe_yaml_load
tests/fixtures/*
```

- `--baseline previous.json` 只输出新增问题。
- SARIF 可直接上传到 GitHub Code Scanning。

## 目录结构

```
safeorbit/
  adapters.py        SDK 与框架接入
  approval.py        写操作审批票
  auth.py            账号、角色、SSO
  backup.py          SQLite 在线备份与恢复
  circuit.py         出站依赖熔断器
  codeaudit.py       静态代码审计
  dashboard.py       单页控制台
  disposition.py     处置闭环
  eval.py            红队与轨迹评测
  guard.py           输入/输出/RAG 内容安全
  infrascan.py       Dockerfile / K8s 配置审计
  gateway.py         模型网关轨迹
  judge.py           LLM 语义裁判
  logging_config.py  结构化日志与请求 ID
  mcp_proxy.py       MCP stdio 代理
  mcp_http_proxy.py  MCP HTTP 代理
  model_settings.py  模型连接与密钥保护
  normalize.py       归一化与证据链
  ops.py             指标与操作审计日志
  otel.py            OTLP JSON/protobuf
  policy.py          策略
  report.py          单轨迹报告
  retention.py       数据保留与证据包
  sca.py             依赖扫描与 SBOM
  rules/             规则引擎
  security.py        边界与限流
  server.py          FastAPI 服务
  store.py           轨迹与报告版本存储
  summary.py         汇总报告
tests/               回归测试
```

## 工程质量

针对《SafeOrbit 项目代码质量分析报告》的整改情况：

| 编号 | 问题 | 处理 |
| --- | --- | --- |
| P-01 / P-05 | 日志覆盖低、异常静默 | store / guard / disposition / auth / codeaudit / security 补日志；证据链与归属冲突失败带 `trace_id` 记录 |
| P-03 | 锁在模块导入时创建 | 改为按当前事件循环惰性创建，避免跨 loop 绑定 |
| P-04 | 同步 SQLite 阻塞事件循环 | 落库、汇总聚合、五视图聚合改走 `anyio.to_thread` |
| P-06 | 弃用的 `on_event` | 迁移到 lifespan，启动校验与后台维护任务统一托管 |
| P-08 / P-17 / P-18 | 无锁文件、无 .gitignore、Docker 层缓存差 | 增加 `constraints.txt`、`requirements.txt`、`.gitignore`，Docker 先装依赖再装本体 |
| P-09 | 无 CI | 新增 `.github/workflows/ci.yml`：安装、语法检查、ruff 语法级规则、测试、构建镜像 |
| P-10 | Host 缺失可绕过 Origin 校验 | `check_csrf` 在缺少 Host 时直接拒绝 |
| P-14 / P-15 / P-16 | Cookie 策略、会话不续期、过期靠被动清理 | `SameSite=strict`；距过期 1 小时内滑动续期（绝对上限 7 天，5 分钟节流）；后台任务周期清理过期审批与隔离 |
| P-19 | 裁判重试次数硬编码 | `JudgeConfig.max_retries` / `SAFEORBIT_JUDGE_MAX_RETRIES` |
| P-21 | YAML 用字符串包含判断 | 改为 `yaml.safe_load_all` 按结构判断清单类型，注释中的 `kind:` 不再误报 |
| P-22 | 表名拼接进 PRAGMA | 增加表名白名单，非登记表直接拒绝 |

暂缓项与原因：

- **P-02 拆分 3,000 行文件**：属于结构性重构，需要单独迭代；本次先补齐日志与护栏，保证拆分时测试能兜底。
- **P-11 时间语义统一**：剩余 naive `datetime.now()` 仅用于报告 ID 与文件名，不参与时间比较；统一到 UTC 感知时间需要历史数据迁移，放在下一阶段。
- **P-12 跨平台密钥保护**：Linux 下缺少加密原语依赖，建议容器部署通过环境变量注入密钥并配合磁盘加密。
- **P-13 迁移框架**：当前仅加列的演进路径可用，删列/改类型需要独立设计。
- **P-20 缓存 token 计价**：现有口径已按“缓存单价优先”计算，但不同厂商对 prompt 是否含缓存 token 的定义不同，需要按厂商补适配器。

## 已知边界

- SQLite 单实例部署；多副本需要换成共享数据库与分布式事件总线。
- 限流、熔断、审批票与隔离状态均为进程内状态，仅在单副本部署下具备完整语义；多副本时实际阈值会按副本数放大。
- 容器部署（Linux）下模型密钥请通过环境变量注入；本地加密存储依赖 Windows DPAPI。审计数据库建议配合磁盘加密（LUKS / BitLocker）与最小权限文件访问。
- MCP 代理是进程内控制，不是不可绕过的强制沙箱。
- Webhook 仅允许公网 HTTPS 443，内网地址会被出站策略拒绝。
- SSO 采用可信反向代理断言模式，未内置 OIDC 授权码流程。
- 策略与处置按全局唯一的 Agent ID 归属，未按租户做同名 Agent 隔离。
- 静态代码审计是规则型扫描，不替代人工复核与动态测试。
- 内置依赖漏洞库是示例数据，生产需同步 OSV/NVD 并通过 `SAFEORBIT_SCA_ADVISORIES` 更新。
- 内容安全 Guard 以高置信度规则为主，复杂语义攻击仍需结合红队和人工复核。
- 评测套件提供红队检出率与轨迹指标，不包含完整的 Agent 自动执行器。

## 测试

```powershell
py -m pytest -q
```

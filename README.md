# grok-dot

尼日利亚销售组织 AI 原生升级 · 常驻智能体平台（POC）。对应技术方案 v0.3 第 20 节的两周 POC：
先把**治理层**（工具代理、策略与审批、审计、记忆）、**模型网关**、**任务编排**和**管理控制台**跑通，
智能体运行时暂用确定性的“技能执行器”，Hermes Agent 在 POC 确认接口后接入（见 `backend/app/bots/hermes_adapter.py`）。

## 端口

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| 后端 API | 8100 | 本机 8000 已被其他进程占用，前后端都不使用 8000 |
| 控制台（前端） | 5173 | `strictPort`：端口被占用时直接报错，不会自动换端口 |

## 运行

```bash
# 后端（Python 3.11+；示例用 uv）
cd backend
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --port 8100

# 控制台
cd frontend
npm install
npm run dev        # http://localhost:5173 ，/api 代理到 8100
```

未设置模型密钥时，所有模型调用会沿降级链落到 `mock-local`（离线模拟输出），整条链路照常可测。
接入真实模型：设置 `QWEN_API_KEY`（可选 `QWEN_BASE_URL`），或 `DEEPSEEK_API_KEY`。

可选环境变量：`NGAP_DB`（SQLite 路径）、`NGAP_SCHEDULER=0`（关闭后台调度）、`FEISHU_WEBHOOK_URL`（把卡片同时推到飞书群机器人）、`NGAP_BOT_SECRET`（Bot 令牌密钥）。

## 测试

```bash
cd backend && .venv/bin/python -m pytest -q
# 在 PostgreSQL 上跑同一套测试：
NGAP_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db .venv/bin/python -m pytest -q
```

## 部署与演示

- 服务器部署：[docs/DEPLOY.md](docs/DEPLOY.md)（Docker Compose：PostgreSQL + 后端 + Caddy HTTPS + 每日备份）
- WhatsApp 演示：[docs/DEMO.md](docs/DEMO.md)

## 目录

```text
backend/
  app/
    api/            控制台接口（/api）与给 Hermes 的内部接口（/v1/chat/completions、/internal/*）
    gateway/        模型网关：路由、数据分级、预算、降级、熔断、脱敏
    tools/          工具代理与连接器（knowledge、competitor-watch、sales-data、pricing-draft、notify、bus、memory）
    orchestrator/   任务台账、例程调度、Bot 间消息队列
    bots/           岗位登记表、技能库、技能执行器、Hermes 接入点
    approvals.py    审批：哈希绑定、升级 / 过期、自动预审、批量批准
    audit.py        审计哈希链
    memory.py       记忆服务
    policy.py       策略引擎与作用域求交集
  config/           policy.yaml、model-routing.yaml、task-policy.yaml、bus-whitelist.yaml、岗位配置档、技能
  data/knowledge/   示例知识库
frontend/           管理控制台（React + Vite）
```

## 与技术方案的对应

| 方案章节 | 实现 |
| --- | --- |
| 5 模型网关 | `app/gateway/service.py` |
| 7 连接器 | `app/tools/connectors/*` |
| 8 策略与审批、8.7 自动预审 | `app/policy.py`、`app/approvals.py` |
| 9 记忆、23.9 个人层 | `app/memory.py` |
| 11.6 代办权限 | `app/tools/proxy.py`（作用域求交集）、`notify_internal`（只发给下达人） |
| 12 审计、23.3 可复现 | `app/audit.py`（哈希链、版本号） |
| 3.4 任务规划与跟进 | `app/orchestrator/tasks.py`、`app/bots/runner.py` |
| 4.4 例程调度与补偿 | `app/orchestrator/scheduler.py` |
| 4.5 Bot 间通信 | `app/orchestrator/bus.py` |

## POC 尚未覆盖

WhatsApp 网关与对外岗位（第三阶段）、Hermes 实际接入、容器隔离与网络策略（部署层）、独立审查模型（目前为规则版预审）、
飞书交互卡片回调（目前卡片在控制台“消息卡片”页查看）、OpenTelemetry / SIEM 对接。

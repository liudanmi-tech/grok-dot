# 服务器部署手册（单机版）

一台云服务器上用 Docker Compose 运行 4 个服务：

| 服务 | 作用 | 对外端口 |
| --- | --- | --- |
| `caddy` | HTTPS（自动申请证书）、控制台静态页、转发 `/api` 和 `/webhooks` | 80、443 |
| `backend` | 后端（模型网关、治理层、WhatsApp 网关、调度）；只读根文件系统、去掉所有 Linux 能力 | 不对外 |
| `db` | PostgreSQL 16（数据卷持久化） | 不对外 |
| `backup` | 每天 `pg_dump` 一次到 `deploy/backups/`，保留 14 天 | — |

访问控制：
- `https://<域名>/webhooks/whatsapp`：公开，由后端校验 Meta 签名；
- 控制台和 `/api`：Caddy 基本认证（用户名 / 密码）。控制台里的“切换用户”仍是 POC 模拟登录，正式给多人使用前要换成飞书登录；
- `/internal`、`/v1`（给 Hermes 的内部接口）：不对公网转发。

## 一、你需要准备的

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | 云服务器 1 台 | 4 核 CPU / 16 GB 内存 / 100 GB SSD，Ubuntu 24.04，固定公网 IP。区域建议 AWS 开普敦（af-south-1）或 Azure 南非北部，最终以法务意见为准 |
| 2 | 安全组 / 防火墙 | 入站只开 80、443（全网）和 22（只对你的 IP）；出站放开（需要访问 Meta、通义千问、Let's Encrypt、GitHub、Docker Hub） |
| 3 | 域名 | 一个子域名，如 `agents.你的公司.com`，A 记录指向服务器 IP |
| 4 | GitHub 访问 | 仓库是私有的话，在服务器上配置只读的 Deploy Key |
| 5 | 密钥 | 通义千问 Key；WhatsApp 的 Phone number ID、长期 Access Token（Meta 系统用户生成）、App Secret |
| 6 | 登录方式 | 告诉我用哪种：给我一个 SSH 账号（你配置我的公钥），或者你按下面步骤自己执行 |

## 二、部署步骤（在服务器上执行）

```bash
# 1. 安装 Docker（官方脚本）
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# 2. 拉代码
sudo mkdir -p /opt/grok-dot && sudo chown $USER /opt/grok-dot
git clone https://github.com/liudanmi-tech/grok-dot.git /opt/grok-dot
cd /opt/grok-dot && git checkout feat/poc-governance

# 3. 填写两个配置文件（不会提交到 Git）
cp backend/.env.example backend/.env     # 模型与 WhatsApp 密钥
cp deploy/.env.example deploy/.env       # 域名、数据库密码、控制台密码
openssl rand -hex 24                      # 生成 PROXY_SECRET 和 POSTGRES_PASSWORD 各一次
docker run --rm caddy:2.8-alpine caddy hash-password --plaintext '你的控制台密码'   # 生成 CONSOLE_PASSWORD_HASH
nano backend/.env
nano deploy/.env

# 4. 把演示用的 WhatsApp 号码登记到 backend/config/dealers.yaml（见 docs/DEMO.md）

# 5. 启动
./scripts/deploy.sh
```

启动后：
- 打开 `https://<域名>`，输入控制台用户名和密码；
- 在 Meta 后台 WhatsApp → Configuration → Webhook 填 `https://<域名>/webhooks/whatsapp`，Verify token 与 `backend/.env` 的 `WA_VERIFY_TOKEN` 一致，订阅 `messages`。

## 三、日常运维

```bash
cd /opt/grok-dot/deploy
docker compose ps                       # 状态
docker compose logs -f backend          # 后端日志
./../scripts/deploy.sh                  # 更新代码并重建
ls backups/                             # 每日备份
# 从备份恢复：
gunzip -c backups/ngap-YYYYMMDD-HHMM.sql.gz | docker compose exec -T db psql -U ngap ngap
```

## 四、还没做的（下一步）

- 飞书登录替换模拟登录；按用户真实权限收窄数据。
- Hermes 接入与每个岗位一个强隔离容器（gVisor）+ 出口白名单代理。
- 数据库迁移工具（目前启动时自动建表，只适用于新增表 / 字段）。
- 监控告警接入、OpenTelemetry。

#!/usr/bin/env bash
# 演示用：把本机后端 8100 暴露为公网 HTTPS，供 Meta WhatsApp Webhook 回调。
# 后端已限制：经隧道进来的请求只放行 /webhooks/whatsapp，控制台接口不会暴露。
# 用完请 Ctrl+C 关闭隧道。免费版 ngrok 每次启动地址会变，需要到 Meta 后台重新填写回调地址。
set -euo pipefail
exec ngrok http 8100

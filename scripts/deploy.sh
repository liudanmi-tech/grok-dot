#!/usr/bin/env bash
# 在服务器上执行：拉取最新代码并重建服务
set -euo pipefail
cd "$(dirname "$0")/.."
BRANCH="${BRANCH:-feat/poc-governance}"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
test -f backend/.env || { echo "缺少 backend/.env（模型与 WhatsApp 密钥）"; exit 1; }
test -f deploy/.env || { echo "缺少 deploy/.env（域名、数据库密码等）"; exit 1; }
cd deploy
docker compose up -d --build
docker compose ps

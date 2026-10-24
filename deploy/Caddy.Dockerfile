# 构建控制台静态文件，交给 Caddy 提供（自动申请 HTTPS 证书）
FROM node:20-alpine AS console
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

FROM caddy:2.8-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=console /src/dist /srv/console

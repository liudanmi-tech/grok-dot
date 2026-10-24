"""启动入口：uvicorn app.main:app --port 8100（8000 已被本机其他进程占用）。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import internal, routes, whatsapp
from .platform import Platform

LOCAL_HOSTS = {"localhost", "127.0.0.1", "testserver", "::1", "[::1]"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(platform: Platform | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.platform.start_background()
        yield
        app.state.platform.stop()

    app = FastAPI(title="grok-dot · 智能体平台 POC", version="0.1.0", lifespan=lifespan)
    app.state.platform = platform or Platform()
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    @app.middleware("http")
    async def public_only_webhooks(request: Request, call_next):
        # 经隧道或公网进来的请求（Host 不是本机）只放行 WhatsApp Webhook；
        # 控制台接口目前是 POC 模拟登录，绝不能暴露到公网。
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in LOCAL_HOSTS and not request.url.path.startswith("/webhooks/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        return await call_next(request)

    app.include_router(routes.router)
    app.include_router(internal.router)
    app.include_router(whatsapp.router)
    return app


app = create_app()

"""启动入口：uvicorn app.main:app --port 8100（8000 已被本机其他进程占用）。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import internal, routes
from .platform import Platform

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
    app.include_router(routes.router)
    app.include_router(internal.router)
    return app


app = create_app()

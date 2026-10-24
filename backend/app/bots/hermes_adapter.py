"""Hermes Agent 接入点（POC 第 1 天确认接口后实现）。

设计约定（技术方案 4.1–4.4）：
- 一个 Bot = 一个 Hermes 实例 = 一个容器；Hermes 只配置两个出口：模型网关和工具代理。
- 调度器通过签名 Webhook 下发任务：POST {hermes_url}/tasks  {task_id, goal, skill, trace_id}
- Hermes 侧插件通过本平台的内部接口工作（见 app/api/internal.py）：
    POST /v1/chat/completions                    模型调用（OpenAI 兼容，请求头带 X-Bot-Id / X-Bot-Token / X-Trace-Id）
    POST /internal/tools/call                    工具调用（经工具代理：白名单、作用域、策略、审计）
    POST /internal/tasks/{task_id}/plan          提交计划（大任务等待下达人确认）
    POST /internal/tasks/{task_id}/steps/{i}     回报步骤进度（完成须有对应工具调用记录）
    POST /internal/tasks/{task_id}/deliver       交付产出
- Bot 令牌：HMAC-SHA256(NGAP_BOT_SECRET, bot_id)，POC 用；生产由凭证库签发 1 小时短期令牌。
"""
from __future__ import annotations

import hashlib
import hmac
import os


def bot_token(bot_id: str) -> str:
    secret = os.environ.get("NGAP_BOT_SECRET", "dev-only-secret").encode()
    return hmac.new(secret, bot_id.encode(), hashlib.sha256).hexdigest()


def verify_bot_token(bot_id: str, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(bot_token(bot_id), token)


class HermesRuntime:
    """占位：POC 确认 Hermes 调用接口后实现 start(task_id)，与 SkillRunner 接口一致。"""

    def __init__(self, base_urls: dict[str, str]):
        self.base_urls = base_urls

    def start(self, task_id: str) -> None:  # pragma: no cover
        raise NotImplementedError("Hermes 调用接口待 POC 第 1 天确认（技术方案 25.1 第 4 项）")

"""模型提供方适配。mock 用于离线开发与测试；openai_compat 对接任意 OpenAI 兼容接口。"""
from __future__ import annotations

import json
import os
import random
import re
import time

import httpx


class ProviderError(Exception):
    def __init__(self, msg: str, *, status: int | None = None, configured: bool = True):
        super().__init__(msg)
        self.status = status
        self.configured = configured  # False = 未配置密钥，不计入熔断


def estimate_tokens(text: str) -> int:
    # 粗略估算：中文约 1 字 1 token，英文约 4 字符 1 token
    cjk = len(re.findall(r"[一-鿿]", text))
    return cjk + max(0, len(text) - cjk) // 4 + 1


class _Resp:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


def post_with_deadline(url: str, *, headers: dict, payload: dict, timeout: float) -> _Resp:
    """带总时长上限的 POST。

    httpx 的 timeout 只限制单次读取间隔；OpenRouter 在非流式请求中会持续发送空白保活，
    导致单次读取永不超时、请求无限挂起。这里按墙钟时间截止，超时即抛 TimeoutException。
    """
    deadline = time.monotonic() + timeout
    per_read = httpx.Timeout(min(timeout, 30.0))
    with httpx.Client(timeout=per_read) as c, c.stream("POST", url, headers=headers, json=payload) as r:
        chunks = []
        for chunk in r.iter_bytes():
            chunks.append(chunk)
            if time.monotonic() > deadline:
                raise httpx.TimeoutException(f"超过总时长 {timeout:.0f}s")
        return _Resp(r.status_code, b"".join(chunks))


RETRY_STATUS = {429, 502, 503}


def post_retrying(url: str, *, headers: dict, payload: dict, timeout: float, attempts: int = 3) -> _Resp:
    """限流（429）与上游临时错误重试，指数退避加抖动；总时长仍受 timeout 约束。"""
    deadline = time.monotonic() + timeout
    for i in range(attempts):
        left = deadline - time.monotonic()
        if left <= 1:
            raise httpx.TimeoutException("重试期间超过总时长")
        try:
            r = post_with_deadline(url, headers=headers, payload=payload, timeout=left)
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
            if i == attempts - 1:
                raise  # 网络瞬断：重试；最后一次仍失败再交给降级链
            time.sleep(min(left / 2, 1.0 + random.random()))
            continue
        if r.status_code not in RETRY_STATUS or i == attempts - 1:
            return r
        time.sleep(min(left / 2, 1.5 * 2 ** i + random.random()))
    return r


class MockProvider:
    """确定性的模拟模型：把输入中的关键行整理成要点，便于端到端联调。"""

    def __init__(self) -> None:
        self.fail_models: set[str] = set()

    def chat(self, entry: dict, messages: list[dict], timeout: float) -> dict:
        if entry["id"] in self.fail_models:
            raise ProviderError(f"mock failure for {entry['id']}", status=503)
        prompt = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
        bullets = [f"- {ln[:160]}" for ln in lines[:8]]
        content = (
            f"【模拟输出 · {entry['id']}】真实模型暂不可用（未配置或调用失败），以下为输入要点整理，仅供联调：\n"
            + "\n".join(bullets)
        )
        return {
            "content": content,
            "input_tokens": sum(estimate_tokens(m.get("content", "")) for m in messages),
            "output_tokens": estimate_tokens(content),
        }


class OpenAICompatProvider:
    def chat(self, entry: dict, messages: list[dict], timeout: float) -> dict:
        key = os.environ.get(entry.get("api_key_env", ""), "")
        if not key:
            raise ProviderError(f"{entry['id']} 未配置密钥（{entry.get('api_key_env')}）", configured=False)
        base = os.environ.get(entry.get("base_url_env", ""), entry.get("base_url_default", "")).rstrip("/")
        try:
            r = post_with_deadline(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                payload={"model": entry.get("upstream_model", entry["id"]), "messages": messages},
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"{entry['id']} 请求失败：{e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise ProviderError(f"{entry['id']} 返回 {r.status_code}", status=r.status_code)
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError(f"{entry['id']} 返回了无法解析的内容", status=502) from e
        usage = data.get("usage") or {}
        content = data["choices"][0]["message"].get("content") or ""
        return {
            "content": content,
            "input_tokens": usage.get("prompt_tokens") or sum(estimate_tokens(m["content"]) for m in messages),
            "output_tokens": usage.get("completion_tokens") or estimate_tokens(content),
        }


class OpenRouterProvider:
    """OpenRouter（OpenAI 兼容）。Key 与各档位模型来自控制台设置；返回 OpenRouter 实际计费的成本。"""

    BASE = "https://openrouter.ai/api/v1"

    def __init__(self, settings_getter):
        self.settings = settings_getter

    def chat(self, entry: dict, messages: list[dict], timeout: float) -> dict:
        cfg = self.settings()
        key = cfg.get("api_key")
        if not key:
            raise ProviderError(f"{entry['id']} 未配置 OpenRouter Key", configured=False)
        model = cfg.get("models", {}).get(entry.get("slot")) or entry.get("upstream_model")
        try:
            r = post_retrying(
                f"{self.BASE}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "HTTP-Referer": "https://github.com/liudanmi-tech/grok-dot",
                         "X-Title": "grok-dot"},
                payload={"model": model, "messages": messages, "usage": {"include": True},
                         **({"reasoning": {"enabled": False}} if entry.get("reasoning") is False else {})},
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"{entry['id']}（{model}）请求失败：{e.__class__.__name__}") from e
        if r.status_code >= 400:
            try:
                msg = (r.json().get("error") or {}).get("message", "")
            except ValueError:
                msg = r.text[:200]
            raise ProviderError(f"{entry['id']}（{model}）返回 {r.status_code}：{msg[:200]}", status=r.status_code)
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError(f"{entry['id']}（{model}）返回了无法解析的内容", status=502) from e
        if data.get("error"):
            raise ProviderError(f"{entry['id']}（{model}）错误：{str(data['error'])[:200]}", status=502)
        usage = data.get("usage") or {}
        content = data["choices"][0]["message"].get("content") or ""
        return {
            "content": content,
            "input_tokens": usage.get("prompt_tokens") or sum(estimate_tokens(m["content"]) for m in messages),
            "output_tokens": usage.get("completion_tokens") or estimate_tokens(content),
            "cost_usd": usage.get("cost"),
            "upstream_model": data.get("model") or model,
        }

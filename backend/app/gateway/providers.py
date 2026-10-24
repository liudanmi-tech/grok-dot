"""模型提供方适配。mock 用于离线开发与测试；openai_compat 对接任意 OpenAI 兼容接口。"""
from __future__ import annotations

import os
import re

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
            f"【模拟输出 · {entry['id']}】未配置真实模型密钥，以下为输入要点整理，仅供联调：\n"
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
            r = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": entry.get("upstream_model", entry["id"]), "messages": messages},
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"{entry['id']} 请求失败：{e.__class__.__name__}") from e
        if r.status_code >= 400:
            raise ProviderError(f"{entry['id']} 返回 {r.status_code}", status=r.status_code)
        data = r.json()
        usage = data.get("usage") or {}
        content = data["choices"][0]["message"].get("content") or ""
        return {
            "content": content,
            "input_tokens": usage.get("prompt_tokens") or sum(estimate_tokens(m["content"]) for m in messages),
            "output_tokens": usage.get("completion_tokens") or estimate_tokens(content),
        }

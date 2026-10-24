"""出网前脱敏（技术方案 5.4）。占位符映射只存在于单次请求内存中，不写日志。"""
from __future__ import annotations

import re

# 顺序有意义：先邮箱，再手机号（11 位，0 或 +234 开头），再 NIN（11 位），最后 10 位 NUBAN 账号
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?234[\s-]?|0)[789][01]\d[\s-]?\d{3}[\s-]?\d{4}(?!\d)")),
    ("NIN", re.compile(r"(?<!\d)\d{11}(?!\d)")),
    ("ACCT", re.compile(r"(?<!\d)\d{10}(?!\d)")),
]

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*\S{12,}"),
    re.compile(r"EAA[A-Za-z0-9]{20,}"),  # Meta 访问令牌样式
]


def redact(text: str) -> tuple[str, dict[str, str], dict[str, int]]:
    mapping: dict[str, str] = {}
    hits: dict[str, int] = {}
    for label, pat in PATTERNS:
        def _sub(m: re.Match, label=label) -> str:
            hits[label] = hits.get(label, 0) + 1
            key = f"<{label}_{hits[label]}>"
            mapping[key] = m.group(0)
            return key
        text = pat.sub(_sub, text)
    return text, mapping, hits


def restore(text: str, mapping: dict[str, str]) -> str:
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


def contains_secret(text: str) -> bool:
    return any(p.search(text) for p in SECRET_PATTERNS)

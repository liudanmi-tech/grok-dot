"""策略引擎与作用域（技术方案 8.1 / 8.2 / 11.6 / 23.7）。"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

AUTO, APPROVAL, HANDOFF, FORBIDDEN = "auto", "approval", "handoff", "forbidden"
# 冲突时的优先级：禁止 > 转交给人 > 审批 > 自动
PRIORITY = [FORBIDDEN, HANDOFF, APPROVAL, AUTO]


@dataclass
class Decision:
    tier: str
    matched: str | None
    unmatched: bool = False


class PolicyEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tiers: dict[str, list[str]] = cfg.get("tiers", {})
        self.approvers: dict[str, dict] = cfg.get("approvers", {})
        self.ttl: dict[str, dict] = cfg.get("ttl", {})
        self.version = f"policy@{cfg.get('version', 0)}"

    def decide(self, action_class: str) -> Decision:
        for tier in PRIORITY:
            for pattern in self.tiers.get(tier, []):
                if fnmatchcase(action_class, pattern):
                    return Decision(tier, pattern)
        # 未匹配：按审批处理，并由调用方告警运营补齐策略
        return Decision(APPROVAL, None, unmatched=True)

    def approver_for(self, action_class: str) -> dict:
        return self.approvers.get(action_class, {"role": "agent_ops", "backup": "agent_ops"})

    def ttl_for(self, action_class: str) -> dict:
        return self.ttl.get(action_class) or self.ttl.get("default") or {"minutes": 1440, "escalate_minutes": 1200}


# ---------- 作用域 ----------
# 作用域是 {维度: [取值...]}；缺省维度表示不限，"*" 表示全部。

Scope = dict[str, list[str]]


def _norm(v: Any) -> list[str] | None:
    if v is None or v == "*" or v == ["*"]:
        return None  # 不限
    if isinstance(v, str):
        return [v]
    return list(v)


def intersect(a: Scope | None, b: Scope | None) -> Scope | None:
    """两个作用域求交集；任一维度交集为空时返回 None（拒绝）。"""
    a = a or {}
    b = b or {}
    out: Scope = {}
    for key in set(a) | set(b):
        va, vb = _norm(a.get(key)), _norm(b.get(key))
        if va is None and vb is None:
            continue
        if va is None:
            out[key] = vb  # type: ignore[assignment]
        elif vb is None:
            out[key] = va
        else:
            both = [x for x in va if x in vb]
            if not both:
                return None
            out[key] = both
    return out


def allows(scope: Scope, key: str, value: str) -> bool:
    allowed = _norm(scope.get(key))
    return allowed is None or value in allowed

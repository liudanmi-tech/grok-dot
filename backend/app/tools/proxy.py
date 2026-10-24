"""工具代理：Bot 的所有工具调用都经过这里（技术方案 3.2 / 8.2 / 11.6）。

顺序：工具白名单 → 策略判定（禁止 / 转交 / 审批 / 自动）→ 作用域求交集 → 入参校验 → 执行或建审批单 → 审计。
"""
from __future__ import annotations

from typing import Any

from ..approvals import ApprovalService
from ..audit import AuditLog
from ..bots.registry import BotRegistry
from ..policy import APPROVAL, FORBIDDEN, HANDOFF, PolicyEngine, _norm, intersect
from .registry import Tool, ToolContext, ToolError, ToolRegistry, validate


def _digest(obj: Any, limit: int = 300) -> Any:
    """审计只存摘要：行数、来源、时点，以及截断后的入参。"""
    if isinstance(obj, dict):
        d = {k: obj[k] for k in ("source", "as_of", "count", "ref", "draft_id", "top_ref") if k in obj}
        for k, v in obj.items():
            if isinstance(v, list):
                d[f"{k}_rows"] = len(v)
        return d or {"keys": list(obj)[:10]}
    s = str(obj)
    return s[:limit]


class ToolProxy:
    def __init__(self, tools: ToolRegistry, bots: BotRegistry, policy: PolicyEngine,
                 approvals: ApprovalService, audit: AuditLog):
        self.tools = tools
        self.bots = bots
        self.policy = policy
        self.approvals = approvals
        self.audit = audit
        self.handoff_listeners: list = []
        approvals.executor = self._execute_approved

    def call(self, bot_id: str, tool_name: str, args: dict, *, trace_id: str, task_id: str | None = None,
             requester: str | None = None, requester_scope: dict | None = None) -> dict:
        bot = self.bots.get(bot_id)
        audit_base = {"bot_id": bot_id, "trace_id": trace_id, "tool": tool_name, "task_id": task_id}
        try:
            if tool_name not in bot.tools:
                raise ToolError("SCOPE_DENIED", f"{bot_id} 未被授权使用 {tool_name}")
            tool = self.tools.get(tool_name)
            decision = self.policy.decide(tool.action_class)
            if decision.tier == FORBIDDEN:
                self.audit.append("alert", level="P1", kind="forbidden_action", **audit_base)
                raise ToolError("FORBIDDEN_ACTION", f"{tool.action_class} 属于禁止动作")
            if decision.unmatched:
                self.audit.append("alert", level="P3", kind="policy.unmatched", action_class=tool.action_class,
                                  **audit_base)

            # 作用域：人工下达的任务取 Bot 权限 ∩ 下达人权限（11.6）
            scope = intersect(bot.scope, requester_scope) if requester_scope is not None else dict(bot.scope)
            if scope is None:
                raise ToolError("SCOPE_DENIED", "下达人无权查看该范围的数据")
            args = self._narrow(tool, dict(args or {}), scope)
            validate(tool.schema, args)
            ctx = ToolContext(bot_id=bot_id, trace_id=trace_id, scope=scope, task_id=task_id, requester=requester,
                              outputs=[o["to"] for o in bot.outputs], kb_visibility=bot.kb_visibility)

            if decision.tier in (APPROVAL, HANDOFF):
                desc = tool.describe(args, ctx) if tool.describe else {"target": None, "evidence": []}
                if decision.tier == HANDOFF:
                    self.audit.append("tool.handoff", args=args, **audit_base)
                    for fn in self.handoff_listeners:
                        fn(task_id, tool_name, args, desc)
                    return {"status": "handoff", "message": "已转交负责人处理"}
                payload = {**args, **({"_preview": desc["preview"]} if desc.get("preview") else {}),
                           **({"_review_text": desc["review_text"]} if desc.get("review_text") else {})}
                rec = self.approvals.create(
                    bot_id=bot_id, action_class=tool.action_class, tool=tool_name, target=desc.get("target"),
                    payload=payload, evidence=desc.get("evidence", []), trace_id=trace_id, task_id=task_id,
                    scope=scope,
                )
                self.audit.append("tool.call", decision="approval", approval_id=rec["approval_id"], args=args,
                                  **audit_base)
                return {"status": "pending_approval" if rec["state"] == "pending" else rec["state"],
                        "approval_id": rec["approval_id"], "review": rec["review"]}

            result = tool.handler(args, ctx)
            self.audit.append("tool.call", decision="allow", args=args, output=_digest(result), scope=scope,
                              **audit_base)
            return {"status": "ok", "result": result}
        except ToolError as e:
            self.audit.append("policy.deny" if e.code in ("SCOPE_DENIED", "FORBIDDEN_ACTION") else "tool.error",
                              code=e.code, error=str(e), **audit_base)
            return {"status": "denied" if e.code in ("SCOPE_DENIED", "FORBIDDEN_ACTION") else "error",
                    "code": e.code, "error": str(e)}

    @staticmethod
    def _narrow(tool: Tool, args: dict, scope: dict) -> dict:
        """入参中的作用域维度只能在作用域之内：取交集，交集为空即拒绝。未传则由连接器按作用域过滤。"""
        for key in tool.scope_keys:
            if key not in args or args[key] in (None, [], ""):
                continue
            allowed = _norm(scope.get(key))
            if allowed is None:
                continue
            wanted = args[key] if isinstance(args[key], list) else [args[key]]
            kept = [v for v in wanted if v in allowed]
            if not kept:
                raise ToolError("SCOPE_DENIED", f"{key}={wanted} 超出可访问范围 {allowed}")
            args[key] = kept if isinstance(args[key], list) else kept[0]
        return args

    def _execute_approved(self, rec: dict) -> dict:
        tool = self.tools.get(rec["tool"])
        args = {k: v for k, v in rec["payload"]["args"].items() if not k.startswith("_")}
        bot = self.bots.get(rec["bot_id"])
        ctx = ToolContext(bot_id=rec["bot_id"], trace_id=rec["trace_id"] or "", scope=rec["payload"]["scope"],
                          task_id=rec["task_id"], outputs=[o["to"] for o in bot.outputs],
                          kb_visibility=bot.kb_visibility)
        result = tool.handler(args, ctx)
        self.audit.append("tool.call", bot_id=rec["bot_id"], trace_id=rec["trace_id"], tool=rec["tool"],
                          decision="approved", approval_id=rec["approval_id"], args=args, output=_digest(result))
        return result

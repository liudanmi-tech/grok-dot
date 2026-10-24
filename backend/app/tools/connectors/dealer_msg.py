"""dealer-msg 连接器：经销商助理只能经这里读本会话、读本经销商档案、提交回复（技术方案 7.2 / 13.3）。

会话和经销商由 WhatsApp 网关注入作用域（conversation_id、dealer_id），模型无法指定号码或别的经销商。
"""
from __future__ import annotations

import hashlib
import json

from ...channels.whatsapp import LANG_NAMES, WhatsAppGateway
from ..registry import Tool, ToolContext, ToolError, ToolRegistry
from .knowledge import KnowledgeBase
from .sales_data import SKUS


def _one(ctx: ToolContext, key: str) -> str:
    vals = ctx.scope.get(key)
    if not vals or len(vals) != 1:
        raise ToolError("SCOPE_DENIED", f"缺少会话作用域 {key}")
    return vals[0]


class DealerMsg:
    def __init__(self, gateway: WhatsAppGateway, kb: KnowledgeBase):
        self.gw = gateway
        self.kb = kb

    def _profile(self, dealer_id: str) -> dict:
        d = next((x for x in self.gw.dealers if x["dealer_id"] == dealer_id), None)
        if not d:
            raise ToolError("NOT_FOUND", "经销商不存在")
        stock = []
        for sku, brand, model in SKUS:
            h = int(hashlib.md5(f"{dealer_id}:{sku}".encode()).hexdigest(), 16)
            stock.append({"sku": sku, "brand": brand, "model": model, "units_in_store": h % 41})
        return {"dealer_id": d["dealer_id"], "name": d["name"], "region": d["region"], "tier": d["tier"],
                "preferred_language": LANG_NAMES.get(d.get("lang", "en")), "store_stock": stock,
                "source": "dealer master data（演示数据）"}

    def get_dealer_profile(self, args: dict, ctx: ToolContext) -> dict:
        return self._profile(_one(ctx, "dealer_id"))

    def get_conversation(self, args: dict, ctx: ToolContext) -> dict:
        cid = _one(ctx, "conversation_id")
        msgs = [{"from": "dealer" if m["direction"] == "in" else "us", "text": m["text"]}
                for m in self.gw.messages(cid, limit=10)]
        return {"messages": msgs, "count": len(msgs), "source": "whatsapp"}

    def describe_reply(self, args: dict, ctx: ToolContext) -> dict:
        cid, did = _one(ctx, "conversation_id"), _one(ctx, "dealer_id")
        profile = self._profile(did)
        last_in = next((m["text"] for m in reversed(self.gw.messages(cid, limit=10)) if m["direction"] == "in"), "")
        evidence = [{"ref": f"dealer:{did}", "text": json.dumps(profile, ensure_ascii=False)}]
        for ref in args.get("kb_refs") or []:
            evidence.append({"ref": ref, "text": self.kb.text_for_ref(ref) or ""})
        return {"target": f"{did} {profile['name']}",
                "preview": f"经销商来信：{last_in}\n\n拟回复（{LANG_NAMES.get(args.get('lang', 'en'))}）：{args['text']}",
                "review_text": args["text"], "evidence": evidence}

    def submit_reply(self, args: dict, ctx: ToolContext) -> dict:
        """审批通过后执行：经网关发出。"""
        cid = _one(ctx, "conversation_id")
        try:
            return self.gw.send(cid, args["text"], kind="reply", task_id=ctx.task_id)
        except RuntimeError as e:
            raise ToolError("UPSTREAM_ERROR", str(e)) from e

    def handoff_to_human(self, args: dict, ctx: ToolContext) -> dict:
        conv = self.gw.get_conversation(_one(ctx, "conversation_id"))
        self.gw.handoff(conv, args.get("reason", "Bot 请求转人工"))
        return {"status": "handoff"}


def register(reg: ToolRegistry, dm: DealerMsg) -> None:
    reg.register(Tool(name="dealer-msg.get_dealer_profile", action_class="read:dealer_self",
                      handler=dm.get_dealer_profile, description="读取本经销商档案与本店库存",
                      schema={"type": "object", "additionalProperties": False, "properties": {}}))
    reg.register(Tool(name="dealer-msg.get_conversation", action_class="read:conversation",
                      handler=dm.get_conversation, description="读取当前会话最近 10 条消息",
                      schema={"type": "object", "additionalProperties": False, "properties": {}}))
    reg.register(Tool(
        name="dealer-msg.submit_reply", action_class="send:dealer_message", handler=dm.submit_reply,
        describe=dm.describe_reply, description="提交回复，渠道经理审批后由 WhatsApp 网关发出",
        schema={"type": "object", "required": ["text"], "additionalProperties": False, "properties": {
            "text": {"type": "string", "maxLength": 1500}, "lang": {"type": "string"},
            "kb_refs": {"type": "array"}, "intent": {"type": "string"}}},
    ))
    reg.register(Tool(name="dealer-msg.handoff_to_human", action_class="notify:internal",
                      handler=dm.handoff_to_human, description="转渠道经理人工处理",
                      schema={"type": "object", "additionalProperties": False,
                              "properties": {"reason": {"type": "string", "maxLength": 300}}}))

"""平台内置工具：内部通知、Bot 间消息、记忆提议。"""
from __future__ import annotations

from ...channels.outbox import Outbox
from ...memory import MemoryRejected, MemoryService
from ...orchestrator.bus import BusDenied, MessageBus
from ..registry import Tool, ToolContext, ToolError, ToolRegistry


def register(reg: ToolRegistry, *, outbox: Outbox, bus: MessageBus, memory: MemoryService,
             namespace_of) -> None:
    def notify_internal(args: dict, ctx: ToolContext) -> dict:
        # 人工任务的产出只发给下达人本人（11.6）；例程只发到岗位配置中登记的群
        allowed = [f"user:{ctx.requester}"] if ctx.requester else list(ctx.outputs)
        to = args.get("to") or allowed[0]
        if to not in allowed:
            raise ToolError("SCOPE_DENIED", f"不允许发送到 {to}，允许：{allowed}")
        mid = outbox.post(recipient=to, kind="message",
                          card={"title": args["title"], "body": args["body"], "bot_id": ctx.bot_id,
                                "trace_id": ctx.trace_id})
        return {"message_id": mid, "to": to}

    def bus_send(args: dict, ctx: ToolContext) -> dict:
        try:
            return bus.send(ctx.bot_id, args["to_bot"], args["intent"], args["payload_ref"],
                            trace_id=ctx.trace_id, scope=ctx.scope)
        except BusDenied as e:
            raise ToolError("SCOPE_DENIED", str(e)) from e

    def bus_read(args: dict, ctx: ToolContext) -> dict:
        msgs = bus.read(ctx.bot_id, trace_id=ctx.trace_id)
        return {"messages": msgs, "count": len(msgs), "source": "bus"}

    def memory_propose(args: dict, ctx: ToolContext) -> dict:
        ns = f"user/{ctx.requester}" if args.get("about") == "requester" and ctx.requester else namespace_of(ctx.bot_id)
        try:
            m = memory.propose(namespace=ns, type=args["type"], content=args["content"], source=ctx.trace_id,
                               bot_id=ctx.bot_id, trace_id=ctx.trace_id)
        except MemoryRejected as e:
            raise ToolError("INVALID_ARGS", str(e)) from e
        return {"memory_id": m["memory_id"], "status": m["status"]}

    reg.register(Tool(
        name="notify.notify_internal", action_class="notify:internal", handler=notify_internal,
        description="向内部群或下达人发送消息卡片",
        schema={"type": "object", "required": ["title", "body"], "additionalProperties": False, "properties": {
            "title": {"type": "string", "maxLength": 200}, "body": {"type": "string", "maxLength": 20000},
            "to": {"type": "string"}}},
    ))
    reg.register(Tool(
        name="bus.send", action_class="bus:send", handler=bus_send, description="向白名单内的其他岗位转交产出引用",
        schema={"type": "object", "required": ["to_bot", "intent", "payload_ref"], "additionalProperties": False,
                "properties": {"to_bot": {"type": "string"}, "intent": {"type": "string"},
                               "payload_ref": {"type": "string"}}},
    ))
    reg.register(Tool(
        name="bus.read", action_class="read:bus", handler=bus_read, description="读取其他岗位转交给本岗位的消息",
        schema={"type": "object", "additionalProperties": False, "properties": {}},
    ))
    reg.register(Tool(
        name="memory.propose", action_class="memory:propose", handler=memory_propose,
        description="提议一条偏好 / 方法 / 摘要类记忆（业务事实不进记忆）",
        schema={"type": "object", "required": ["type", "content"], "additionalProperties": False, "properties": {
            "type": {"type": "string", "enum": ["preference", "method", "summary"]},
            "content": {"type": "string", "maxLength": 1000},
            "about": {"type": "string", "enum": ["bot", "requester"]}}},
    ))

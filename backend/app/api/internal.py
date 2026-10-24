"""给外部智能体运行时（Hermes）用的内部接口：所有调用仍经工具代理、任务台账与模型网关。"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from ..bots.hermes_adapter import verify_bot_token
from ..gateway.service import BudgetExceeded, GatewayError
from ..orchestrator.tasks import TaskError

router = APIRouter()


def _bot(request: Request, bot_id: str | None, token: str | None):
    p = request.app.state.platform
    if not bot_id or bot_id not in p.bots.bots or not verify_bot_token(bot_id, token):
        raise HTTPException(401, "Bot 身份校验失败")
    return p


class ChatIn(BaseModel):
    model: str | None = "auto"
    messages: list[dict]


@router.post("/v1/chat/completions")
def chat(body: ChatIn, request: Request, x_bot_id: str | None = Header(None), x_bot_token: str | None = Header(None),
         x_trace_id: str | None = Header(None), x_task_type: str | None = Header(None),
         x_language: str | None = Header(None), x_data_class: str = Header("internal")):
    p = _bot(request, x_bot_id, x_bot_token)
    try:
        out = p.gateway.chat(x_bot_id, body.messages, task_type=x_task_type, language=x_language,
                             data_class=x_data_class, trace_id=x_trace_id)
    except BudgetExceeded as e:
        raise HTTPException(429, str(e)) from e
    except GatewayError as e:
        raise HTTPException(502, str(e)) from e
    return {
        "id": f"chatcmpl-{x_trace_id or 'na'}", "object": "chat.completion", "model": out["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": out["content"]}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": out["input_tokens"], "completion_tokens": out["output_tokens"],
                  "total_tokens": out["input_tokens"] + out["output_tokens"]},
    }


class ToolCallIn(BaseModel):
    tool: str
    args: dict = {}
    trace_id: str
    task_id: str | None = None


@router.post("/internal/tools/call")
def tool_call(body: ToolCallIn, request: Request, x_bot_id: str | None = Header(None),
              x_bot_token: str | None = Header(None)):
    p = _bot(request, x_bot_id, x_bot_token)
    requester, scope = None, None
    if body.task_id:
        task = p.tasks.get(body.task_id)
        if task["bot_id"] != x_bot_id:
            raise HTTPException(403, "任务不属于该 Bot")
        requester, scope = task["requester"], task["requester_scope"]
    return p.proxy.call(x_bot_id, body.tool, body.args, trace_id=body.trace_id, task_id=body.task_id,
                        requester=requester, requester_scope=scope)


class PlanIn(BaseModel):
    steps: list[dict]
    reason: str | None = None


@router.post("/internal/tasks/{task_id}/plan")
def plan(task_id: str, body: PlanIn, request: Request, x_bot_id: str | None = Header(None),
         x_bot_token: str | None = Header(None)):
    p = _bot(request, x_bot_id, x_bot_token)
    try:
        return p.tasks.submit_plan(task_id, body.steps, by="bot", reason=body.reason)
    except TaskError as e:
        raise HTTPException(400, str(e)) from e


class StepIn(BaseModel):
    status: str
    note: str | None = None


@router.post("/internal/tasks/{task_id}/steps/{index}")
def step(task_id: str, index: int, body: StepIn, request: Request, x_bot_id: str | None = Header(None),
         x_bot_token: str | None = Header(None)):
    p = _bot(request, x_bot_id, x_bot_token)
    try:
        return p.tasks.step(task_id, index, body.status, body.note)
    except TaskError as e:
        raise HTTPException(400, str(e)) from e


class DeliverIn(BaseModel):
    outputs: list[dict]


@router.post("/internal/tasks/{task_id}/deliver")
def deliver(task_id: str, body: DeliverIn, request: Request, x_bot_id: str | None = Header(None),
            x_bot_token: str | None = Header(None)):
    p = _bot(request, x_bot_id, x_bot_token)
    return p.tasks.deliver(task_id, body.outputs)

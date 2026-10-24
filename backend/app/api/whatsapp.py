"""WhatsApp Webhook 与控制台的会话接口。"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .routes import P, current_user

router = APIRouter()


@router.get("/webhooks/whatsapp")
def verify(mode: str = Query(None, alias="hub.mode"), token: str = Query(None, alias="hub.verify_token"),
           challenge: str = Query(None, alias="hub.challenge")):
    if mode == "subscribe" and token and token == os.environ.get("WA_VERIFY_TOKEN"):
        return PlainTextResponse(challenge or "")
    raise HTTPException(403, "verify token mismatch")


@router.post("/webhooks/whatsapp")
async def inbound(request: Request, x_hub_signature_256: str | None = Header(None)):
    p = request.app.state.platform
    body = await request.body()
    if not p.whatsapp.verify_signature(body, x_hub_signature_256):
        p.audit.append("alert", level="P2", kind="whatsapp.bad_signature")
        raise HTTPException(401, "bad signature")
    n = p.whatsapp.handle_webhook(await request.json())
    return {"ok": True, "messages": n}  # 尽快返回 200，处理在后台线程完成


@router.get("/api/wa/status")
def status(p=Depends(P)):
    return {"live": p.whatsapp.live, "killswitch": p.whatsapp.killswitch(),
            "dealers": [{k: d[k] for k in ("dealer_id", "name", "region", "lang", "phones")} for d in p.whatsapp.dealers]}


@router.get("/api/wa/conversations")
def conversations(p=Depends(P)):
    return p.whatsapp.list_conversations()


@router.get("/api/wa/conversations/{cid}")
def conversation(cid: str, p=Depends(P)):
    try:
        conv = p.whatsapp.get_conversation(cid)
    except KeyError as e:
        raise HTTPException(404, "会话不存在") from e
    pending = [a for a in p.approvals.list(state="pending,escalated,returned", limit=500)
               if a["tool"] == "dealer-msg.submit_reply" and cid in (a["payload"]["scope"].get("conversation_id") or [])]
    tasks = [t for t in p.tasks.list(bot_id="dealer-assistant", limit=50)
             if t["context"].get("conversation_id") == cid][:5]
    return {**conv, "dealer": p.whatsapp.dealer_of(conv), "window_open": p.whatsapp.window_open(conv),
            "messages": p.whatsapp.messages(cid, limit=100), "pending": pending,
            "tasks": [{k: t[k] for k in ("task_id", "status", "status_label", "progress_done", "progress_total",
                                         "notes", "created_at")} for t in tasks]}


class SimIn(BaseModel):
    phone: str
    text: str


@router.post("/api/wa/simulate")
def simulate(body: SimIn, p=Depends(P), user=Depends(current_user)):
    """模拟经销商发来一条消息（Meta 未开通时演示用；走与真实 Webhook 完全相同的处理）。"""
    return p.whatsapp.receive(body.phone, body.text, simulated=True)


class ReplyIn(BaseModel):
    text: str


@router.post("/api/wa/conversations/{cid}/reply")
def human_reply(cid: str, body: ReplyIn, p=Depends(P), user=Depends(current_user)):
    """人工接手后直接回复（同样经网关发出并留痕）。"""
    if not {"channel_manager", "channel_lead"} & set(user["roles"]):
        raise HTTPException(403, "只有渠道经理可以人工回复")
    try:
        return p.whatsapp.send(cid, body.text, kind="human")
    except (RuntimeError, KeyError) as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/wa/conversations/{cid}/release")
def release(cid: str, p=Depends(P), user=Depends(current_user)):
    p.whatsapp.release(cid, user["id"])
    return {"ok": True}


class KillIn(BaseModel):
    on: bool


@router.post("/api/wa/killswitch")
def kill(body: KillIn, p=Depends(P), user=Depends(current_user)):
    if not {"agent_ops", "channel_manager", "country_manager"} & set(user["roles"]):
        raise HTTPException(403, "无权操作熔断")
    p.whatsapp.set_killswitch(body.on, user["id"])
    return {"killswitch": body.on}

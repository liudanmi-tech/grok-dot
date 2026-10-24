"""控制台与渠道使用的 HTTP 接口。POC 用请求头 X-User-Id 识别员工（生产环境为 SSO）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..approvals import ApprovalError
from ..memory import MemoryRejected
from ..orchestrator.tasks import TaskError

router = APIRouter(prefix="/api")


def P(request: Request):
    return request.app.state.platform


def current_user(request: Request, x_user_id: str | None = Header(default=None)) -> dict:
    users = request.app.state.platform.users
    if not x_user_id or x_user_id not in users:
        raise HTTPException(401, "未识别的用户（请求头 X-User-Id）")
    return users[x_user_id]


def guard(fn, *a, **kw) -> Any:
    try:
        return fn(*a, **kw)
    except (TaskError, ApprovalError, MemoryRejected, KeyError, ValueError) as e:
        raise HTTPException(400, str(e).strip("'")) from e


# ---------- 基础 ----------
@router.get("/health")
def health(p=Depends(P)):
    return {"ok": True, "time": p.clock.now().isoformat(), "audit": p.audit.verify()["ok"]}


@router.get("/me")
def me(user=Depends(current_user)):
    return user


@router.get("/users")
def users(p=Depends(P)):
    return list(p.users.values())


# ---------- 岗位登记表（23.2） ----------
ATTENTION = ("awaiting_confirmation", "needs_human", "paused", "awaiting_approval")
OPEN = ("registered", "planning", "awaiting_confirmation", "running", "awaiting_subtask", "awaiting_approval", "needs_human",
        "paused", "rework")


def _with_links(p, t: dict) -> dict:
    """任务卡片附上父子关系：总调度的任务带分工进度，成员的子任务带来源。"""
    if t["parent_task_id"]:
        try:
            parent = p.tasks.get(t["parent_task_id"])
            t["parent"] = {"task_id": parent["task_id"], "bot_id": parent["bot_id"], "goal": parent["goal"],
                           "bot_name": p.bots.get(parent["bot_id"]).display_name}
        except Exception:  # noqa: BLE001
            t["parent"] = None
    kids = p.tasks.children(t["task_id"]) if p.bots.template_of(t["bot_id"]) == "chief-of-staff" else []
    t["children"] = [{"task_id": c["task_id"], "bot_id": c["bot_id"], "goal": c["goal"], "status": c["status"],
                      "progress_done": c["progress_done"], "progress_total": c["progress_total"],
                      "cost_usd": c["cost_usd"], "approvals": c["approvals"], "updated_at": c["updated_at"],
                      "last_note": c["notes"][-1]["text"] if c["notes"] else None,
                      "preview": (c["outputs"][0]["content"][:240] if c["outputs"] else None)} for c in kids]
    return t


def _bot_card(p, b) -> dict:
    """团队成员卡片：身份、在岗状态、正在做什么、需要你处理什么、今日产出与费用。"""
    meta = p.bots.meta.get(b.template, {})
    tasks = p.db.query("SELECT task_id, goal, status, progress_done, progress_total, updated_at, source FROM tasks"
                       " WHERE bot_id=? ORDER BY updated_at DESC LIMIT 30", [b.id])
    current = next((t for t in tasks if t["status"] in OPEN), None)
    attention = [t for t in tasks if t["status"] in ATTENTION]
    approvals = p.db.one("SELECT COUNT(*) n FROM approvals WHERE bot_id=? AND state IN ('pending','escalated')",
                         [b.id])["n"]
    day = p.clock.now().isoformat()[:10]
    done_today = sum(1 for t in tasks if t["status"] in ("delivered", "accepted") and t["updated_at"][:10] == day)
    if not b.active:
        activity = b.state
    elif attention or approvals:
        activity = "needs_you"
    elif current:
        activity = "working"
    else:
        activity = "idle"
    budget = p.gateway.cfg.get("budgets", {})
    return {
        "id": b.id, "template": b.template, "display_name": b.display_name, "title": meta.get("title"),
        "color": meta.get("color", "#64748b"), "autonomy": meta.get("autonomy"), "channel": meta.get("channel", "内部"),
        "state": b.state, "stage": b.status, "scope": b.scope, "tools": b.tools, "triggers": b.triggers,
        "activity": activity, "current": current, "attention": len(attention), "approvals": approvals,
        "done_today": done_today, "tasks_total": len(tasks), "month_spend": round(p.gateway.month_spend(b.id), 4),
        "budget": budget.get(b.id, budget.get(b.template, budget.get("default"))),
        "created_by": b.created_by, "created_at": b.created_at,
    }


@router.get("/bots")
def bots(include_retired: bool = False, p=Depends(P)):
    return [_bot_card(p, b) for b in p.bots.list(include_retired=include_retired)]


@router.get("/bots/{bot_id}")
def bot_detail(bot_id: str, p=Depends(P)):
    try:
        b = p.bots.get(bot_id)
    except KeyError as e:
        raise HTTPException(404, "成员不存在") from e
    card = _bot_card(p, b)
    meta = p.bots.meta.get(b.template, {})
    card.update({
        "summary": meta.get("summary"), "capabilities": meta.get("capabilities", []), "outputs": b.outputs,
        "limits": b.limits, "kb_visibility": b.kb_visibility, "owner": b.owner, "business_owner": b.business_owner,
        "service_account": b.service_account, "namespace": b.namespace,
        "skills": p.skills.for_template(b.template),
        "memories": p.memory.list(namespace=b.namespace)[:20],
        "tasks": [_with_links(p, t) for t in p.tasks.list(bot_id=b.id, limit=30)],
        "bus_routes": [r for r in p.bus.routes if b.template in (r["from"], r["to"])],
    })
    return card


@router.get("/bot-templates")
def bot_templates(p=Depends(P)):
    return p.bots.library()


class HireIn(BaseModel):
    template: str
    display_name: str | None = None
    region: str | None = None


HIRE_ROLES = {"agent_ops", "country_manager"}


@router.post("/bots")
def hire(body: HireIn, p=Depends(P), user=Depends(current_user)):
    if not HIRE_ROLES & set(user["roles"]):
        raise HTTPException(403, "只有智能体运营或国家经理可以招募成员")
    b = guard(p.bots.hire, body.template, actor=user["id"], display_name=body.display_name, region=body.region)
    p.audit.append("bot.hire", bot_id=b.id, actor=user["id"], template=b.template, scope=b.scope)
    return _bot_card(p, b)


class BotPatch(BaseModel):
    state: str | None = None
    display_name: str | None = None


@router.patch("/bots/{bot_id}")
def patch_bot(bot_id: str, body: BotPatch, p=Depends(P), user=Depends(current_user)):
    if not HIRE_ROLES & set(user["roles"]):
        raise HTTPException(403, "只有智能体运营或国家经理可以管理成员")
    if body.display_name:
        guard(p.bots.rename, bot_id, body.display_name)
    if body.state:
        guard(p.bots.set_state, bot_id, body.state)
    p.audit.append("bot.update", bot_id=bot_id, actor=user["id"], state=body.state, display_name=body.display_name)
    return _bot_card(p, p.bots.get(bot_id))


@router.get("/overview")
def overview(p=Depends(P), user=Depends(current_user)):
    day = p.clock.now().isoformat()[:10]
    team = p.bots.list()
    tasks_today = p.db.query("SELECT status FROM tasks WHERE created_at >= ?", [day])
    roles = set(user["roles"])
    my_approvals = [a for a in p.approvals.list(state="pending,escalated", limit=500)
                    if a["approver_role"] in roles or (a["state"] == "escalated" and a["backup_role"] in roles)]
    my_tasks = p.tasks.list(requester=user["id"], limit=200)
    inbox = {
        "approvals": [{"approval_id": a["approval_id"], "bot_id": a["bot_id"], "action_class": a["action_class"],
                       "target": a["target"], "review": (a.get("review") or {}).get("level"),
                       "created_at": a["created_at"], "task_id": a["task_id"]} for a in my_approvals[:20]],
        "confirm": [{"task_id": t["task_id"], "bot_id": t["bot_id"], "goal": t["goal"], "status": t["status"]}
                    for t in my_tasks if t["status"] == "awaiting_confirmation"],
        "review": [{"task_id": t["task_id"], "bot_id": t["bot_id"], "goal": t["goal"], "status": t["status"]}
                   for t in my_tasks if t["status"] == "delivered" and not t["parent_task_id"]][:10],
        "human": [{"task_id": t["task_id"], "bot_id": t["bot_id"], "goal": t["goal"], "status": t["status"]}
                  for t in p.tasks.list(status="needs_human", limit=50)],
    }
    return {
        "team_size": len(team), "active": sum(1 for b in team if b.active),
        "tasks_today": len(tasks_today),
        "done_today": sum(1 for t in tasks_today if t["status"] in ("delivered", "accepted")),
        "running": p.db.one("SELECT COUNT(*) n FROM tasks WHERE status IN ('running','planning','registered')")["n"],
        "spend_month": round(p.gateway.month_spend(), 4),
        "budget_month": p.gateway.cfg.get("budgets", {}).get("global"),
        "audit_ok": p.audit.verify()["ok"],
        "inbox": inbox,
        "inbox_count": sum(len(v) for v in inbox.values()),
    }


@router.get("/activity")
def activity(limit: int = 60, bot_id: str | None = None, p=Depends(P)):
    """团队动态：把审计事件压成可读的一行。"""
    names = {b.id: b.display_name for b in p.bots.list(include_retired=True)}
    keep = {"task.create", "task.plan", "task.status", "tool.call", "approval.create", "approval.decide",
            "bus.send", "model.fallback", "bot.hire", "bot.update", "message.inbound", "message.outbound", "alert",
            "memory.write"}
    rows = [e for e in p.audit.list(bot_id=bot_id, limit=limit * 4) if e["event_type"] in keep][:limit]
    out = []
    for e in rows:
        pl = e["payload"]
        out.append({"ts": e["ts"], "type": e["event_type"], "bot_id": e["bot_id"],
                    "bot_name": names.get(e["bot_id"], e["bot_id"]), "trace_id": e["trace_id"],
                    "task_id": pl.get("task_id"), "data": {k: pl.get(k) for k in (
                        "tool", "decision", "to_status", "from_status", "note", "goal", "action_class", "approval_id",
                        "to_bot", "intent", "level", "kind", "actor", "template", "state", "from_model", "to_model",
                        "steps", "large", "source", "review_level", "channel", "subtasks", "by") if pl.get(k) is not None}})
    return out


@router.get("/skills")
def skills(p=Depends(P)):
    return p.skills.list()


@router.get("/tools")
def tools(p=Depends(P)):
    return p.tools.specs()


# ---------- 任务 ----------
class TaskIn(BaseModel):
    bot_id: str
    goal: str
    deadline: str | None = None


@router.post("/tasks")
def create_task(body: TaskIn, p=Depends(P), user=Depends(current_user)):
    if "requester" not in user["roles"]:
        raise HTTPException(403, "无权下达任务")
    task = guard(p.tasks.create, bot_id=body.bot_id, goal=body.goal, deadline=body.deadline, source="manual",
                 requester=user["id"], requester_scope=user.get("scope", {}))
    p.runner.start(task["task_id"])
    return p.tasks.get(task["task_id"])


@router.get("/tasks")
def list_tasks(bot_id: str | None = None, status: str | None = None, mine: bool = False, p=Depends(P),
               user=Depends(current_user)):
    return p.tasks.list(bot_id=bot_id, status=status, requester=user["id"] if mine else None)


@router.get("/tasks/{task_id}")
def get_task(task_id: str, p=Depends(P)):
    task = guard(p.tasks.get, task_id)
    task["timeline"] = list(reversed(p.audit.list(trace_id=task["trace_id"], limit=500)))
    return task


class ActionIn(BaseModel):
    action: str            # confirm / modify / cancel / pause / resume / topup / handback / takeover / useful / rework
    note: str | None = None


@router.post("/tasks/{task_id}/action")
def task_action(task_id: str, body: ActionIn, p=Depends(P), user=Depends(current_user)):
    t, uid, a = p.tasks, user["id"], body.action
    fn = {
        "confirm": lambda: t.confirm(task_id, actor_id=uid, decision="approve"),
        "modify": lambda: t.confirm(task_id, actor_id=uid, decision="modify", note=body.note),
        "cancel": lambda: t.cancel(task_id, actor_id=uid),
        "pause": lambda: t.pause(task_id, actor_id=uid),
        "resume": lambda: t.resume(task_id, actor_id=uid),
        "topup": lambda: t.topup(task_id, actor_id=uid),
        "handback": lambda: t.handback(task_id, actor_id=uid, note=body.note),
        "takeover": lambda: t.handback(task_id, actor_id=uid, note=body.note, take_over=True),
        "useful": lambda: t.feedback(task_id, actor_id=uid, verdict="useful"),
        "rework": lambda: t.feedback(task_id, actor_id=uid, verdict="rework", note=body.note),
    }.get(a)
    if not fn:
        raise HTTPException(400, f"未知操作 {a}")
    return guard(fn)


class FollowupIn(BaseModel):
    text: str
    bot_id: str | None = None   # 群聊里 @ 某成员；为空则交给总调度再分工


@router.post("/tasks/{task_id}/followup")
def followup(task_id: str, body: FollowupIn, p=Depends(P), user=Depends(current_user)):
    return guard(p.coordinator.followup, task_id, actor_id=user["id"], text=body.text, bot_id=body.bot_id)


# ---------- 审批 ----------
@router.get("/approvals")
def list_approvals(state: str | None = None, mine: bool = False, p=Depends(P), user=Depends(current_user)):
    if not mine:
        return p.approvals.list(state=state)
    out: dict[str, dict] = {}
    for role in user["roles"]:
        for r in p.approvals.list(state=state, role=role):
            out[r["approval_id"]] = r
    return sorted(out.values(), key=lambda r: r["created_at"], reverse=True)


class DecideIn(BaseModel):
    decision: str                 # approve / edit / reject
    note: str | None = None
    edited_args: dict | None = None


@router.post("/approvals/{approval_id}/decide")
def decide(approval_id: str, body: DecideIn, p=Depends(P), user=Depends(current_user)):
    return guard(p.approvals.decide, approval_id, actor=user, decision=body.decision,
                 edited_args=body.edited_args, note=body.note)


class BatchIn(BaseModel):
    ids: list[str]


@router.post("/approvals/batch-approve")
def batch(body: BatchIn, p=Depends(P), user=Depends(current_user)):
    return guard(p.approvals.batch_approve, body.ids, actor=user)


# ---------- 审计 ----------
@router.get("/audit")
def audit(trace_id: str | None = None, bot_id: str | None = None, event_type: str | None = None,
          limit: int = 200, p=Depends(P)):
    return p.audit.list(trace_id=trace_id, bot_id=bot_id, event_type=event_type, limit=limit)


@router.get("/audit/verify")
def audit_verify(p=Depends(P)):
    return p.audit.verify()


@router.get("/alerts")
def alerts(limit: int = 100, p=Depends(P)):
    return p.audit.list(event_type="alert", limit=limit)


# ---------- 记忆 ----------
@router.get("/memories")
def memories(namespace: str | None = None, status: str | None = None, q: str | None = None, p=Depends(P)):
    return p.memory.list(namespace=namespace, status=status, q=q)


@router.get("/memories/{memory_id}/history")
def memory_history(memory_id: str, p=Depends(P)):
    return p.memory.history(memory_id)


class MemoryIn(BaseModel):
    namespace: str
    type: str
    content: str


@router.post("/memories")
def memory_create(body: MemoryIn, p=Depends(P), user=Depends(current_user)):
    if body.namespace.startswith("user/") and body.namespace != f"user/{user['id']}" and \
            "agent_ops" not in user["roles"]:
        raise HTTPException(403, "只能管理自己的个人层记忆")
    return guard(p.memory.propose, namespace=body.namespace, type=body.type, content=body.content,
                 created_by=user["id"])


class MemoryEditIn(BaseModel):
    reason: str
    content: str | None = None
    status: str | None = None


def _can_manage(user: dict, mem: dict) -> bool:
    return "agent_ops" in user["roles"] or mem["namespace"] == f"user/{user['id']}"


@router.patch("/memories/{memory_id}")
def memory_edit(memory_id: str, body: MemoryEditIn, p=Depends(P), user=Depends(current_user)):
    mem = p.memory.get(memory_id)
    if not mem:
        raise HTTPException(404, "记忆不存在")
    if not _can_manage(user, mem):
        raise HTTPException(403, "只有智能体运营或本人可以修改")
    return guard(p.memory.edit, memory_id, actor=user["id"], reason=body.reason, content=body.content,
                 status=body.status)


@router.post("/memories/{memory_id}/approve")
def memory_approve(memory_id: str, p=Depends(P), user=Depends(current_user)):
    if "agent_ops" not in user["roles"]:
        raise HTTPException(403, "方法类记忆须智能体运营确认")
    return guard(p.memory.approve, memory_id, user["id"])


@router.delete("/memories/{memory_id}")
def memory_delete(memory_id: str, reason: str, hard: bool = False, p=Depends(P), user=Depends(current_user)):
    mem = p.memory.get(memory_id)
    if not mem:
        raise HTTPException(404, "记忆不存在")
    if not _can_manage(user, mem):
        raise HTTPException(403, "只有智能体运营或本人可以删除")
    guard(p.memory.delete, memory_id, actor=user["id"], reason=reason, hard=hard)
    return {"ok": True}


# ---------- 例程、消息、Bot 间通信、草稿、用量 ----------
@router.get("/routines/runs")
def routine_runs(p=Depends(P)):
    return p.scheduler.runs()


@router.post("/routines/{bot_id}/{routine}/run")
def routine_run(bot_id: str, routine: str, p=Depends(P), user=Depends(current_user)):
    if "agent_ops" not in user["roles"] and "country_manager" not in user["roles"]:
        raise HTTPException(403, "无权手动触发例程")
    return guard(p.scheduler.trigger, bot_id, routine)


@router.get("/outbox")
def outbox(recipient: str | None = None, p=Depends(P)):
    return p.outbox.list(recipient=recipient)


@router.get("/bus")
def bus(p=Depends(P)):
    return p.bus.list()


@router.get("/drafts")
def drafts(p=Depends(P)):
    return p.pricing.list()


@router.get("/usage")
def usage(p=Depends(P)):
    return p.gateway.usage_summary()


@router.get("/sources/health")
def source_health(p=Depends(P)):
    return p.competitor.source_health()


@router.get("/config/routing")
def routing(p=Depends(P)):
    cfg = dict(p.gateway.cfg)
    cfg["catalog"] = [{k: v for k, v in m.items() if "env" not in k} for m in cfg["catalog"]]
    return cfg


@router.get("/config/policy")
def policy(p=Depends(P)):
    return p.policy.cfg


# ---------- 模型设置（OpenRouter Key 与三档模型） ----------
SETTINGS_ROLES = {"agent_ops", "country_manager"}
_or_models_cache: dict = {"at": 0.0, "rows": []}


def _require_settings_role(user: dict) -> None:
    if not SETTINGS_ROLES & set(user["roles"]):
        raise HTTPException(403, "只有智能体运营或国家经理可以修改模型设置")


@router.get("/settings/models")
def get_model_settings(p=Depends(P), user=Depends(current_user)):
    s = p.gateway.settings.public()
    s["slots"] = {
        "standard": "标准：经销商回复、经营分析、早报",
        "economy": "经济：分类、自动预审",
        "premium": "高级：本地化内容创作",
    }
    s["can_edit"] = bool(SETTINGS_ROLES & set(user["roles"]))
    return s


class ModelSettingsIn(BaseModel):
    api_key: str | None = None
    clear_key: bool = False
    models: dict | None = None


@router.put("/settings/models")
def put_model_settings(body: ModelSettingsIn, p=Depends(P), user=Depends(current_user)):
    _require_settings_role(user)
    return guard(p.gateway.settings.update, actor=user["id"], at=p.clock.now().isoformat(),
                 api_key=body.api_key, clear_key=body.clear_key, models=body.models)


class ModelTestIn(BaseModel):
    slot: str = "standard"


@router.post("/settings/models/test")
def test_model_settings(body: ModelTestIn, p=Depends(P), user=Depends(current_user)):
    """用所选档位实际调一次 OpenRouter（不走降级链），返回模型、耗时与费用。"""
    import time

    from ..gateway.providers import ProviderError

    _require_settings_role(user)
    entry = next((m for m in p.gateway.catalog.values() if m.get("provider") == "openrouter"
                  and m.get("slot") == body.slot), None)
    if not entry:
        raise HTTPException(400, f"未知档位 {body.slot}")
    t0 = time.monotonic()
    try:
        out = p.gateway.providers["openrouter"].chat(
            entry, [{"role": "user", "content": "Reply with exactly: OK"}], timeout=30)
    except ProviderError as e:
        return {"ok": False, "error": str(e)}
    p.audit.append("settings.test", actor=user["id"], slot=body.slot, model=out.get("upstream_model"))
    return {"ok": True, "model": out.get("upstream_model"), "reply": out["content"][:80],
            "latency_ms": int((time.monotonic() - t0) * 1000), "cost_usd": out.get("cost_usd")}


@router.get("/settings/openrouter/models")
def openrouter_models(q: str | None = None, user=Depends(current_user)):
    """OpenRouter 公开的模型列表（由后端拉取并缓存 10 分钟，按每百万 token 美元价格返回）。"""
    import time

    import httpx

    if time.time() - _or_models_cache["at"] > 600 or not _or_models_cache["rows"]:
        try:
            data = httpx.get("https://openrouter.ai/api/v1/models", timeout=15).json().get("data", [])
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"获取 OpenRouter 模型列表失败：{e.__class__.__name__}") from e
        rows = []
        for m in data:
            pr = m.get("pricing") or {}
            try:
                pin, pout = float(pr.get("prompt", 0)) * 1e6, float(pr.get("completion", 0)) * 1e6
            except (TypeError, ValueError):
                pin = pout = None
            rows.append({"id": m["id"], "name": m.get("name"), "context": m.get("context_length"),
                         "input_per_m": round(pin, 4) if pin is not None else None,
                         "output_per_m": round(pout, 4) if pout is not None else None})
        rows.sort(key=lambda r: r["id"])
        _or_models_cache.update(at=time.time(), rows=rows)
    rows = _or_models_cache["rows"]
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["id"].lower() or ql in (r["name"] or "").lower()]
    return rows

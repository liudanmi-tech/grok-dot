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
@router.get("/bots")
def bots(p=Depends(P)):
    out = []
    for b in p.bots.list():
        open_tasks = p.db.one("SELECT COUNT(*) n FROM tasks WHERE bot_id=? AND status NOT IN "
                              "('accepted','cancelled','failed','delivered')", [b.id])["n"]
        out.append({**b.__dict__, "namespace": b.namespace, "open_tasks": open_tasks,
                    "month_spend": round(p.gateway.month_spend(b.id), 4),
                    "budget": p.gateway.cfg.get("budgets", {}).get(b.id)})
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

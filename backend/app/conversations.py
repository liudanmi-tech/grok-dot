"""任务对话视图：每个顶层任务 = 一个「群」（下达人 + 总调度 + 参与的数字员工）。

只读组装，不改变执行逻辑：消息流来自审计事件（父任务与子任务的 trace），交付物来自任务产出，
待办来自交付卡片里的「下一步」，上下文含目标、权限快照、关联 KPI 与参与成员的记忆。
"""
from __future__ import annotations

import re
from datetime import timezone

from .config import LAGOS, parse_iso
from .orchestrator.coordinator import TEMPLATE as CHIEF

DONE = {"delivered", "accepted"}


def _participants(p, t: dict, kids: list[dict]) -> list[str]:
    ids = [t["bot_id"]] + [k["bot_id"] for k in kids]
    return list(dict.fromkeys(ids))


def summary(p, t: dict) -> dict:
    kids = p.tasks.children(t["task_id"])
    members = _participants(p, t, kids)
    preview = None
    for src in [t, *reversed(kids)]:
        if src["outputs"]:
            o = src["outputs"][0]
            preview = (o.get("card") or {}).get("headline") or o["content"][:120]
            break
    if not preview and t["notes"]:
        preview = t["notes"][-1]["text"]
    if preview:  # 预览去掉 Markdown 符号
        preview = re.sub(r"\s+", " ", re.sub(r"[#*|`>]+|-{3,}|【模拟输出[^】]*】", " ", preview)).strip()[:160]
    ctx = t["context"] or {}
    done = sum(1 for k in kids if k["status"] in DONE)
    return {"task_id": t["task_id"], "goal": t["goal"], "bot_id": t["bot_id"], "status": t["status"],
            "status_label": t["status_label"], "source": t["source"], "requester": t["requester"],
            "created_at": t["created_at"], "updated_at": t["updated_at"],
            "day": parse_iso(t["created_at"]).astimezone(LAGOS).date().isoformat(),
            "members": members, "kpi_id": ctx.get("kpi_id"),
            "progress_done": done if kids else t["progress_done"], "progress_total": len(kids) if kids else t["progress_total"],
            "deliverables": sum(len(x["outputs"]) for x in [t, *kids]), "preview": preview, "cost_usd": round(
                t["cost_usd"] + sum(k["cost_usd"] for k in kids), 4),
            "attention": t["status"] in ("awaiting_confirmation", "needs_human", "awaiting_approval")
            or any(k["status"] in ("needs_human", "awaiting_approval") for k in kids)}


def listing(p, limit: int = 150, kpi_id: str | None = None) -> list[dict]:
    rows = p.db.query("SELECT task_id FROM tasks WHERE parent_task_id IS NULL AND source != 'whatsapp'"
                      " ORDER BY created_at DESC LIMIT ?", [limit])
    out = [summary(p, p.tasks.get(r["task_id"])) for r in rows]
    return [c for c in out if not kpi_id or c["kpi_id"] == kpi_id]


def detail(p, task_id: str) -> dict:
    t = p.tasks.get(task_id)
    kids = p.tasks.children(task_id)
    members = _participants(p, t, kids)
    ctx = t["context"] or {}
    kpi = None
    if ctx.get("kpi_id"):
        try:
            k = p.rhythm._row(ctx["kpi_id"])
            kpi = {"kpi_id": k["kpi_id"], "title": k["title"]}
        except Exception:  # noqa: BLE001
            kpi = None
    return {"task": t, "summary": summary(p, t), "children": kids, "members": members, "kpi": kpi,
            "messages": messages(p, t, kids), "deliverables": deliverables(p, t, kids), "todos": todos(p, t, kids),
            "memories": memories(p, t, members)}


def _name(p, bot_id: str | None) -> str:
    try:
        return p.bots.get(bot_id).display_name
    except Exception:  # noqa: BLE001
        return bot_id or ""


def messages(p, t: dict, kids: list[dict]) -> list[dict]:
    """把审计事件翻译成群聊消息（按时间）。"""
    by_trace = {t["trace_id"]: t, **{k["trace_id"]: k for k in kids}}
    events = []
    for tr in by_trace:
        events += p.audit.list(trace_id=tr, limit=400)
    events.sort(key=lambda e: e["seq"])
    out: list[dict] = []
    started: set[str] = set()
    rounds = 0
    assignments = (t["context"] or {}).get("assignments", [])

    def add(e, kind, author, text, **extra):
        out.append({"ts": e["ts"], "kind": kind, "author": author, "text": text, **extra})

    for e in events:
        task = by_trace[e["trace_id"]]
        pl, et, own = e["payload"], e["event_type"], task["task_id"] == t["task_id"]
        if et == "task.create" and own:
            if task["source"] in ("manual",) and task["requester"]:
                add(e, "user", task["requester"], task["goal"])
            else:
                label = {"routine": "例程自动触发", "standup": "晨会派活", "coordinator": "总调度分派"}.get(task["source"], task["source"])
                add(e, "system", None, f"{label}：{task['goal']}")
        elif et == "task.followup":
            add(e, "user", pl.get("actor"), pl.get("text", ""), mention=pl.get("to_bot"))
        elif et == "task.plan" and own and pl.get("by") == "coordinator":
            picks = [a for a in assignments if a.get("round", 0) == rounds]
            brief = (t["context"] or {}).get("brief") if rounds == 0 else None
            rounds += 1
            add(e, "bot", t["bot_id"], (brief + "\n" if brief else "") +
                (f"派给 {len(picks)} 名成员并行执行。" if picks else "无需新增分工，重新汇总。"),
                assign=[{"bot_id": a["bot_id"], "goal": a["goal"], "task_id": a["task_id"]} for a in picks])
        elif et == "task.plan" and own:
            add(e, "bot", task["bot_id"], f"收到，拆成 {pl.get('steps')} 步开始执行" + ("，大任务需要你确认计划。" if pl.get("large") else "。"))
        elif et == "task.status":
            to, note = pl.get("to_status"), pl.get("note") or ""
            if to == "running" and not own and task["task_id"] not in started:
                started.add(task["task_id"])
                add(e, "bot", task["bot_id"], f"开始干活：{task['goal']}", task_id=task["task_id"])
            elif to == "delivered":
                for i, o in enumerate(task["outputs"]):
                    card = o.get("card") or {}
                    add(e, "deliverable", task["bot_id"], card.get("headline") or o["content"][:160],
                        title=card.get("title") or ("汇总交付" if own else "交付"), task_id=task["task_id"], index=i,
                        has_card=bool(o.get("card")))
                if not task["outputs"]:
                    add(e, "bot", task["bot_id"], f"✅ 完成：{note}", task_id=task["task_id"])
            elif to in ("needs_human", "failed", "awaiting_confirmation", "paused"):
                add(e, "alert", task["bot_id"], note or task["status_label"], task_id=task["task_id"], status=to)
            elif to == "rework" and own:
                add(e, "user", task["requester"], note)
        elif et == "approval.create":
            add(e, "approval", task["bot_id"], f"提交审批：{pl.get('action_class')}", approval_id=pl.get("approval_id"),
                task_id=task["task_id"])
        elif et == "approval.decide":
            add(e, "system", None, f"审批 {pl.get('approval_id')} 已{ {'approve': '批准', 'edit': '修改后批准'}.get(pl.get('decision'), '拒绝') }"
                                   f"（{pl.get('actor')}）")
    return out


def deliverables(p, t: dict, kids: list[dict]) -> list[dict]:
    out = []
    for src in [*kids, t]:
        for i, o in enumerate(src["outputs"]):
            card = o.get("card") or {}
            out.append({"task_id": src["task_id"], "index": i, "bot_id": src["bot_id"], "bot_name": _name(p, src["bot_id"]),
                        "title": card.get("title") or "交付", "headline": card.get("headline") or o["content"][:160],
                        "has_card": bool(o.get("card")), "content": o["content"], "summary": src["task_id"] == t["task_id"],
                        "at": src["updated_at"]})
    return out


def todos(p, t: dict, kids: list[dict]) -> list[dict]:
    """待办：分工进度 + 交付卡片里的下一步。"""
    out = [{"kind": "assignment", "bot_id": k["bot_id"], "text": k["goal"], "status": k["status"],
            "status_label": k["status_label"], "task_id": k["task_id"]} for k in kids]
    for src in [t, *kids]:
        for o in src["outputs"]:
            for a in (o.get("card") or {}).get("actions", []):
                out.append({"kind": "action", "bot_id": src["bot_id"], "owner": a.get("owner"), "text": a.get("action"),
                            "due": a.get("due"), "status": "open"})
    return out


def memories(p, t: dict, members: list[str]) -> list[dict]:
    out = []
    for b in members:
        try:
            ns = p.bots.get(b).namespace
        except Exception:  # noqa: BLE001
            continue
        for m in p.memory.list(namespace=ns)[:4]:
            out.append({"bot_id": b, "type": m["type"], "content": m["content"], "created_at": m.get("created_at")})
    if t["requester"]:
        for m in p.memory.list(namespace=f"user/{t['requester']}")[:4]:
            out.append({"bot_id": None, "type": m["type"], "content": m["content"], "created_at": m.get("created_at")})
    return out


# ---------- 数字员工的成长：反馈与等级 ----------
LEVELS = [(0, "L1 见习"), (60, "L2 初级"), (180, "L3 中级"), (400, "L4 高级"), (800, "L5 专家")]


def growth(p, bot_id: str) -> dict:
    rows = p.db.query("SELECT task_id FROM tasks WHERE bot_id=?", [bot_id])
    tasks = [p.tasks.get(r["task_id"]) for r in rows]
    delivered = sum(1 for x in tasks if x["status"] in DONE)
    accepted = sum(1 for x in tasks if x["status"] == "accepted" or x.get("feedback") == "useful")
    rework = sum(1 for x in tasks if (x.get("feedback") or "").startswith("rework"))
    failed = sum(1 for x in tasks if x["status"] == "failed")
    feedback = [{"ts": x["updated_at"], "source": "任务验收", "score": "good" if x["feedback"] == "useful" else "behind",
                 "comment": "下达人验收：有用" if x["feedback"] == "useful" else x["feedback"].removeprefix("rework: "),
                 "task_id": x["task_id"]} for x in tasks if x.get("feedback")]
    ratings = {"good": 0, "ok": 0, "behind": 0}
    for m in p.db.query("SELECT day, data, host, updated_at FROM meetings WHERE kind='review' AND status='done'"):
        from .db import loads
        d = loads(m["data"], {})
        for r in (d.get("final") or {}).get("ratings", []):
            if r["bot_id"] == bot_id:
                ratings[r["score"]] = ratings.get(r["score"], 0) + 1
                feedback.append({"ts": m["updated_at"], "source": f"晚会点评 {m['day']}", "score": r["score"],
                                 "comment": r.get("comment") or "", "by": m["host"]})
    xp = delivered * 10 + accepted * 5 + ratings["good"] * 20 + ratings["ok"] * 8 - ratings["behind"] * 5 - rework * 5 - failed * 5
    xp = max(0, xp)
    level, nxt = LEVELS[0][1], None
    for i, (need, name) in enumerate(LEVELS):
        if xp >= need:
            level = name
            nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    feedback.sort(key=lambda f: f["ts"] or "", reverse=True)
    return {"level": level, "xp": xp, "next_xp": nxt, "delivered": delivered, "accepted": accepted, "rework": rework,
            "failed": failed, "ratings": ratings, "feedback": feedback[:30], "tasks_total": len(tasks)}


def today_digest(p) -> dict:
    """驾驶舱「今日进展」：今天的任务对话汇总。"""
    day = p.clock.now().astimezone(LAGOS).date().isoformat()
    convs = [c for c in listing(p, limit=200) if c["day"] == day]
    return {"day": day, "conversations": convs,
            "done": sum(1 for c in convs if c["status"] in DONE), "running": sum(1 for c in convs if c["status"] not in DONE
                                                                                and c["status"] not in ("failed", "cancelled")),
            "attention": sum(1 for c in convs if c["attention"]),
            "deliverables": sum(c["deliverables"] for c in convs), "now": p.clock.now().astimezone(timezone.utc).isoformat()}


__all__ = ["listing", "detail", "growth", "today_digest", "CHIEF"]

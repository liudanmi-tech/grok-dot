"""任务台账：登记、计划确认、进度、审批等待、人工接管、预算、卡住检测、验收（技术方案 3.4 / 23.1）。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Callable

from ..audit import AuditLog
from ..bots.registry import BotRegistry
from ..channels.outbox import Outbox
from ..config import Clock, iso, parse_iso
from ..db import DB, dumps, loads

STATUS_LABEL = {
    "registered": "已登记", "planning": "规划中", "awaiting_confirmation": "待确认", "running": "执行中",
    "awaiting_approval": "等待审批", "needs_human": "需要人工处理", "paused": "已暂停", "delivered": "已交付",
    "accepted": "已验收", "rework": "需修改", "cancelled": "已取消", "failed": "失败",
}
TERMINAL = {"accepted", "cancelled", "failed"}
LLM_STEP_COST_ESTIMATE = 0.25  # 美元，按 Qwen-Plus 单次分析的常见量级估算


class TaskError(Exception):
    pass


class TaskService:
    def __init__(self, db: DB, audit: AuditLog, clock: Clock, outbox: Outbox, bots: BotRegistry, cfg: dict):
        self.db = db
        self.audit = audit
        self.clock = clock
        self.outbox = outbox
        self.bots = bots
        self.cfg = cfg
        self.on_resume: Callable[[str], None] | None = None   # 由执行器注入：继续执行
        self.on_retry: Callable[[str], None] | None = None

    # ---------- 读取 ----------
    def get(self, task_id: str) -> dict:
        r = self.db.one("SELECT * FROM tasks WHERE task_id=?", [task_id])
        if not r:
            raise TaskError(f"任务不存在：{task_id}")
        for k, d in (("steps", []), ("outputs", []), ("approvals", []), ("notes", []), ("context", {}),
                     ("requester_scope", None)):
            r[k] = loads(r.get(k), d)
        r["status_label"] = STATUS_LABEL.get(r["status"], r["status"])
        return r

    def list(self, *, bot_id: str | None = None, status: str | None = None, requester: str | None = None,
             limit: int = 200) -> list[dict]:
        sql, args = "SELECT task_id FROM tasks WHERE 1=1", []
        for col, val in (("bot_id", bot_id), ("status", status), ("requester", requester)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        rows = self.db.query(sql + " ORDER BY created_at DESC LIMIT ?", [*args, limit])
        return [self.get(r["task_id"]) for r in rows]

    # ---------- 写入 ----------
    def _update(self, task_id: str, **fields) -> dict:
        now = iso(self.clock.now())
        fields.setdefault("updated_at", now)
        sets, args = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(dumps(v) if isinstance(v, (list, dict)) else v)
        self.db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE task_id=?", [*args, task_id])
        task = self.get(task_id)
        self._card(task)
        return task

    def _touch(self, task_id: str, **fields) -> dict:
        return self._update(task_id, last_activity_at=iso(self.clock.now()), stall_hinted=0, **fields)

    def _note(self, task: dict, text: str) -> list:
        return [*task["notes"], {"ts": iso(self.clock.now()), "text": text}]

    def _status(self, task_id: str, status: str, note: str | None = None, **extra) -> dict:
        task = self.get(task_id)
        fields = {"status": status, **extra}
        if note:
            fields["notes"] = self._note(task, note)
        self.audit.append("task.status", bot_id=task["bot_id"], trace_id=task["trace_id"], task_id=task_id,
                          from_status=task["status"], to_status=status, note=note)
        return self._touch(task_id, **fields)

    def create(self, *, bot_id: str, goal: str, source: str = "manual", requester: str | None = None,
               requester_scope: dict | None = None, deadline: str | None = None, skill: str | None = None,
               parent_task_id: str | None = None) -> dict:
        bot = self.bots.get(bot_id)
        now = iso(self.clock.now())
        budget = self.cfg.get("budget_usd", {})
        task_id = "tsk_" + uuid.uuid4().hex[:10]
        row = {
            "task_id": task_id, "parent_task_id": parent_task_id, "source": source, "requester": requester,
            # 固化下达人数据权限快照（11.6）
            "requester_scope": dumps(requester_scope) if requester_scope is not None else None,
            "bot_id": bot_id, "goal": goal, "deadline": deadline, "skill": skill or bot.default_skill,
            "status": "registered", "budget_usd": budget.get(bot_id, budget.get("default", 2)),
            "trace_id": "trc_" + uuid.uuid4().hex[:12], "card_id": f"task:{task_id}",
            "created_at": now, "updated_at": now, "last_activity_at": now,
        }
        self.db.execute(f"INSERT INTO tasks({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        self.audit.append("task.create", bot_id=bot_id, trace_id=row["trace_id"], task_id=task_id, source=source,
                          requester=requester, goal=goal[:200])
        task = self.get(task_id)
        self._card(task)
        return task

    def is_large(self, steps: list[dict], est_cost_usd: float) -> tuple[bool, list[str]]:
        lt = self.cfg.get("large_task", {})
        reasons = []
        if len(steps) >= lt.get("min_steps", 6):
            reasons.append(f"步骤 {len(steps)} ≥ {lt.get('min_steps', 6)}")
        minutes = sum(s.get("est_minutes", 0) for s in steps)
        if minutes > lt.get("max_est_minutes", 20):
            reasons.append(f"预计 {minutes} 分钟 > {lt.get('max_est_minutes', 20)}")
        if est_cost_usd > lt.get("max_est_cost_usd", 1.0):
            reasons.append(f"预估费用 ${est_cost_usd:.2f} > ${lt.get('max_est_cost_usd', 1.0)}")
        always = set(lt.get("always_if", []))
        if "approval_action" in always and any(s.get("approval_action") for s in steps):
            reasons.append("含二级动作")
        if "cross_bot_subtask" in always and any(s.get("cross_bot") for s in steps):
            reasons.append("含跨岗位子任务")
        return bool(reasons), reasons

    def submit_plan(self, task_id: str, steps: list[dict], *, by: str = "bot", reason: str | None = None) -> dict:
        task = self.get(task_id)
        if task["status"] in TERMINAL:
            raise TaskError("任务已结束")
        rp = self.cfg.get("replan", {})
        replan_count = task["replan_count"]
        if task["plan_version"] >= 1 and by == "bot":
            replan_count += 1
            if replan_count > rp.get("bot_initiated_max", 2):
                return self._status(task_id, "needs_human", "重新规划次数已达上限，转智能体运营处理",
                                    replan_count=replan_count)
        if task["plan_version"] + 1 > rp.get("max_plan_versions", 5):
            return self._status(task_id, "needs_human", "计划版本数已达上限，转智能体运营处理")
        norm = [{"index": i, "id": s.get("id", f"s{i}"), "title": s["title"], "tool": s.get("tool"),
                 "type": s.get("type", "tool"), "est_minutes": s.get("est_minutes", 1),
                 "approval_action": bool(s.get("approval_action")), "cross_bot": bool(s.get("cross_bot")),
                 "status": "pending"} for i, s in enumerate(steps)]
        est_cost = sum(LLM_STEP_COST_ESTIMATE for s in norm if s["type"] == "llm")
        large, why = self.is_large(norm, est_cost)
        needs_confirm = large and task["source"] == "manual"
        status = "awaiting_confirmation" if needs_confirm else "running"
        note = (f"计划 v{task['plan_version'] + 1}（{len(norm)} 步）" + (f"，需确认：{'；'.join(why)}" if needs_confirm
                                                                       else "，小任务直接执行"))
        if reason:
            note += f"；重新规划原因：{reason}"
        self.audit.append("task.plan", bot_id=task["bot_id"], trace_id=task["trace_id"], task_id=task_id,
                          version=task["plan_version"] + 1, steps=len(norm), large=large, reasons=why, by=by)
        return self._status(task_id, status, note, steps=norm, plan_version=task["plan_version"] + 1,
                            progress_done=0, progress_total=len(norm), replan_count=replan_count)

    def _require_requester(self, task: dict, actor_id: str) -> None:
        if task["requester"] and actor_id != task["requester"]:
            raise TaskError("只有下达人可以操作该任务")

    def confirm(self, task_id: str, *, actor_id: str, decision: str, note: str | None = None) -> dict:
        task = self.get(task_id)
        self._require_requester(task, actor_id)
        if task["status"] != "awaiting_confirmation":
            raise TaskError(f"任务当前为「{task['status_label']}」，无需确认")
        if decision == "approve":
            t = self._status(task_id, "running", "下达人已确认计划")
        elif decision == "modify":
            if not note:
                raise TaskError("修改计划须说明要求")
            # 下达人补充要求引起的重新规划不计次（任务参数设定）
            t = self._status(task_id, "planning", f"下达人修改要求：{note}",
                             context={**task["context"], "requester_note": note})
        elif decision == "cancel":
            return self._status(task_id, "cancelled", note or "下达人取消")
        else:
            raise TaskError(f"未知决定 {decision}")
        if self.on_resume:
            self.on_resume(task_id)
        return t

    def step(self, task_id: str, index: int, status: str, note: str | None = None) -> dict:
        task = self.get(task_id)
        steps = task["steps"]
        if index >= len(steps):
            raise TaskError("步骤不存在")
        st = steps[index]
        if status == "done" and st.get("type") == "tool" and st.get("tool"):
            # 声称完成的步骤必须有对应的工具调用记录
            if not self.audit.has_tool_call(task["trace_id"], st["tool"], since=task["created_at"]):
                raise TaskError(f"步骤「{st['title']}」缺少对应的工具调用记录，不能标记完成")
        st["status"] = status
        if note:
            st["note"] = note
        done = sum(1 for s in steps if s["status"] in ("done", "skipped"))
        self.audit.append("task.update", bot_id=task["bot_id"], trace_id=task["trace_id"], task_id=task_id,
                          step=index, status=status, note=note)
        return self._touch(task_id, steps=steps, progress_done=done)

    def save_context(self, task_id: str, context: dict) -> None:
        self.db.execute("UPDATE tasks SET context=? WHERE task_id=?", [dumps(context), task_id])

    def add_cost(self, task_id: str, usd: float) -> dict:
        task = self.get(task_id)
        cost = round(task["cost_usd"] + usd, 6)
        b = self.cfg.get("budget_usd", {})
        warn = b.get("warn_at", 0.8) * task["budget_usd"]
        if cost >= task["budget_usd"]:
            return self._status(task_id, "paused", f"已用 ${cost:.2f}，达到单任务预算 ${task['budget_usd']:.2f}，"
                                                   "请确认是否追加", cost_usd=cost)
        fields: dict = {"cost_usd": cost}
        if task["cost_usd"] < warn <= cost:
            fields["notes"] = self._note(task, f"费用已用到预算的 {int(b.get('warn_at', 0.8) * 100)}%")
        return self._update(task_id, **fields)

    def topup(self, task_id: str, *, actor_id: str) -> dict:
        task = self.get(task_id)
        self._require_requester(task, actor_id)
        ratio = self.cfg.get("budget_usd", {}).get("topup_max_ratio", 1.0)
        base = self.cfg.get("budget_usd", {}).get(task["bot_id"], self.cfg.get("budget_usd", {}).get("default", 2))
        new_budget = round(task["budget_usd"] + base * ratio, 2)
        t = self._status(task_id, "running", f"下达人追加预算至 ${new_budget:.2f}", budget_usd=new_budget)
        if self.on_resume:
            self.on_resume(task_id)
        return t

    def await_approval(self, task_id: str, approval_id: str) -> dict:
        task = self.get(task_id)
        return self._status(task_id, "awaiting_approval", f"等待审批 {approval_id}",
                            approvals=[*task["approvals"], approval_id])

    def link_approval(self, task_id: str, approval_id: str) -> None:
        task = self.get(task_id)
        self._update(task_id, approvals=[*task["approvals"], approval_id])

    def on_approval(self, rec: dict) -> None:
        if not rec.get("task_id"):
            return
        try:
            task = self.get(rec["task_id"])
        except TaskError:
            return
        if task["status"] != "awaiting_approval":
            if rec["state"] in ("rejected", "expired", "returned"):
                self._update(task["task_id"], notes=self._note(task, f"审批 {rec['approval_id']}：{rec['state']}"
                                                               f"{'（' + rec['decision_note'] + '）' if rec.get('decision_note') else ''}"))
            return
        if rec["state"] == "executed":
            self._status(task["task_id"], "running", f"审批 {rec['approval_id']} 已通过并执行")
            if self.on_resume:
                self.on_resume(task["task_id"])
        elif rec["state"] in ("rejected", "expired", "failed", "returned"):
            self._status(task["task_id"], "needs_human", f"审批 {rec['approval_id']} 状态为 {rec['state']}，"
                                                         "请决定后续处理")

    def needs_human(self, task_id: str, reason: str) -> dict:
        return self._status(task_id, "needs_human", f"需要人工：{reason}")

    def handback(self, task_id: str, *, actor_id: str, note: str | None = None, take_over: bool = False) -> dict:
        task = self.get(task_id)
        if task["status"] != "needs_human":
            raise TaskError("任务不在“需要人工处理”状态")
        if take_over:
            return self._status(task_id, "delivered", f"{actor_id} 接手并完成：{note or ''}")
        t = self._status(task_id, "running", f"{actor_id} 已处理并交还：{note or ''}")
        if self.on_resume:
            self.on_resume(task_id)
        return t

    def pause(self, task_id: str, *, actor_id: str) -> dict:
        self._require_requester(self.get(task_id), actor_id)
        return self._status(task_id, "paused", "下达人暂停")

    def resume(self, task_id: str, *, actor_id: str) -> dict:
        task = self.get(task_id)
        self._require_requester(task, actor_id)
        if task["status"] != "paused":
            raise TaskError("任务未暂停")
        t = self._status(task_id, "running", "下达人继续")
        if self.on_resume:
            self.on_resume(task_id)
        return t

    def cancel(self, task_id: str, *, actor_id: str) -> dict:
        self._require_requester(self.get(task_id), actor_id)
        return self._status(task_id, "cancelled", "下达人取消")

    def fail(self, task_id: str, reason: str) -> dict:
        return self._status(task_id, "failed", reason)

    def deliver(self, task_id: str, outputs: list[dict]) -> dict:
        return self._status(task_id, "delivered", "已交付", outputs=outputs)

    def feedback(self, task_id: str, *, actor_id: str, verdict: str, note: str | None = None) -> dict:
        task = self.get(task_id)
        self._require_requester(task, actor_id)
        if task["status"] != "delivered":
            raise TaskError("只有已交付的任务可以验收")
        if verdict == "useful":
            return self._status(task_id, "accepted", "下达人验收：有用", feedback="useful")
        if not note:
            raise TaskError("“需修改”须附意见")
        t = self._status(task_id, "rework", f"下达人要求修改：{note}", feedback=f"rework: {note}",
                         context={**task["context"], "requester_note": note})
        if self.on_resume:
            self.on_resume(task_id)
        return t

    # ---------- 定时：卡住检测、截止时间 ----------
    def tick(self) -> dict:
        st = self.cfg.get("stall", {})
        hint = timedelta(minutes=st.get("hint_after_minutes", 10))
        timeout = timedelta(minutes=st.get("timeout_after_minutes", 20))
        now = self.clock.now()
        hinted = timed_out = overdue = 0
        for t in self.list(status="running", limit=10000):
            idle = now - parse_iso(t["last_activity_at"])
            if idle >= timeout:
                timed_out += 1
                if t["retries"] < st.get("auto_retry", 1):
                    self._status(t["task_id"], "running", f"{int(idle.total_seconds() // 60)} 分钟无进展，自动重试",
                                 retries=t["retries"] + 1)
                    if self.on_retry:
                        self.on_retry(t["task_id"])
                else:
                    self.audit.append("alert", bot_id=t["bot_id"], trace_id=t["trace_id"], level="P3",
                                      kind="task.stalled", task_id=t["task_id"])
                    self.needs_human(t["task_id"], "多次超时，已通知智能体运营")
            elif idle >= hint and not t["stall_hinted"]:
                hinted += 1
                self._update(t["task_id"], stall_hinted=1, notes=self._note(t, "可能卡住：10 分钟无进展"))
        for t in self.list(limit=10000):
            if t["deadline"] and t["status"] not in TERMINAL | {"delivered"} and parse_iso(t["deadline"]) < now \
                    and not any("已超过截止时间" in n["text"] for n in t["notes"]):
                overdue += 1
                self.audit.append("alert", bot_id=t["bot_id"], trace_id=t["trace_id"], level="P3",
                                  kind="task.overdue", task_id=t["task_id"])
                self._update(t["task_id"], notes=self._note(t, "已超过截止时间"))
        return {"hinted": hinted, "timed_out": timed_out, "overdue": overdue}

    # ---------- 进度卡片 ----------
    def _card(self, task: dict) -> None:
        bot = self.bots.get(task["bot_id"])
        recipient = f"user:{task['requester']}" if task["requester"] else (bot.outputs[0]["to"] if bot.outputs else "ops")
        buttons = {
            "awaiting_confirmation": ["查看计划", "批准计划", "修改要求", "取消"],
            "running": ["补充要求", "暂停", "取消"],
            "paused": ["继续", "追加预算", "取消"],
            "needs_human": ["已完成，交还", "我来做完"],
            "delivered": ["有用", "需修改"],
        }.get(task["status"], [])
        self.outbox.post(
            recipient=recipient, kind="task_progress", card_id=task["card_id"],
            card={
                "title": f"{bot.display_name}：{task['goal'][:60]}",
                "task_id": task["task_id"], "status": task["status"], "status_label": task["status_label"],
                "progress": f"{task['progress_done']}/{task['progress_total']}",
                "steps": [{"title": s["title"], "status": s["status"]} for s in task["steps"]],
                "last_note": task["notes"][-1]["text"] if task["notes"] else None,
                "cost": f"${task['cost_usd']:.3f} / ${task['budget_usd']:.2f}",
                "buttons": buttons,
            },
        )

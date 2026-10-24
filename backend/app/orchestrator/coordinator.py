"""总调度（经理 Agent）：一句话目标 → 拆分工 → 派给在岗成员并行执行 → 跟进 → 汇总交付。

借鉴 Relevance AI 的「经理 Agent + 专职成员」与 Grok Bot 的「群聊里 @ 指定负责人」：
- 下达人只和总调度说话；子任务以总调度名义派出，继承下达人的数据权限快照（OBO，11.6）。
- 默认免打扰：子任务不单独给下达人发卡片、不进验收收件箱；只有审批、需要人工、最终交付才找人。
- 子任务状态变化回调到父任务；全部结束后自动汇总。下达人可在同一任务里追加（@成员 或交给总调度再分工）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

from ..gateway.service import BudgetExceeded, GatewayError
from .tasks import TaskError, TaskService

log = logging.getLogger(__name__)

TEMPLATE = "chief-of-staff"
MAX_ASSIGNMENTS = 6
STAGGER_S = 1.0
CHILD_DONE = {"delivered", "accepted", "failed", "cancelled"}
CHILD_WAITING = {"awaiting_approval": "等待审批", "needs_human": "需要人工处理", "paused": "已暂停",
                 "awaiting_confirmation": "待确认"}

# 模型不可用（或返回非 JSON）时的兜底分工：按关键词匹配岗位
KEYWORDS = {
    "market-intel": ["竞品", "对手", "samsung", "redmi", "xiaomi", "新品", "价格带", "市场", "情报"],
    "price-sentinel": ["调价", "促销", "动销", "降价", "价格", "滞销"],
    "biz-review": ["销量", "复盘", "区域", "汇总", "周报", "经营", "出货", "库存"],
    "content-factory": ["文案", "内容", "海报", "宣传", "广告", "社媒", "状态"],
    "voice-of-customer": ["售后", "投诉", "工单", "质量", "故障", "carlcare", "用户声音"],
}


class Coordinator:
    def __init__(self, *, tasks: TaskService, bots, gateway, runner, memory):
        self.tasks = tasks
        self.bots = bots
        self.gateway = gateway
        self.runner = runner
        self.memory = memory
        self._locks: dict[str, threading.RLock] = {}
        runner.special[TEMPLATE] = self.run
        tasks.on_child_status = self.on_child

    # ---------- 团队 ----------
    def roster(self) -> list[dict]:
        out = []
        for b in self.bots.list():
            meta = self.bots.meta.get(b.template, {})
            if not b.active or b.template == TEMPLATE or meta.get("channel", "内部") != "内部":
                continue
            out.append({"bot_id": b.id, "template": b.template, "name": b.display_name,
                        "summary": meta.get("summary", ""), "capabilities": meta.get("capabilities", []),
                        "scope": b.scope})
        return out

    # ---------- 主流程（由执行器在后台线程调用；同一父任务串行） ----------
    def run(self, task_id: str) -> None:
        lock = self._locks.setdefault(task_id, threading.RLock())
        with lock:
            try:
                self._run(task_id)
            except Exception as e:  # noqa: BLE001
                log.exception("coordinator %s crashed", task_id)
                try:
                    self.tasks.fail(task_id, f"总调度异常：{e}")
                except TaskError:
                    pass

    def _run(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        ctx = task["context"] or {}
        if task["status"] in ("registered", "planning"):
            self._plan(task, ctx, ctx.get("requester_note"))
            return
        if task["status"] == "rework":
            note = ctx.get("requester_note") or "请补充完善"
            self._plan(task, ctx, note, followup=True)
            return
        if task["status"] in ("awaiting_subtask", "running"):
            self._maybe_finish(task, ctx)

    # ---------- 规划与分派 ----------
    def _plan(self, task: dict, ctx: dict, note: str | None, *, followup: bool = False, only: str | None = None) -> None:
        tid = task["task_id"]
        team = self.roster()
        if not team:
            self.tasks.needs_human(tid, "团队里没有可分派的在岗成员，请先招募")
            return
        if only:
            member = next((m for m in team if m["bot_id"] == only), None)
            if not member:
                raise TaskError("该成员当前不在岗或不能接内部任务")
            brief, picks = None, [{"bot_id": only, "goal": note, "why": "下达人指定"}]
        elif ctx.get("preset") and not followup:  # 晨会已定好分工：直接派
            valid = {m["bot_id"] for m in team}
            brief, picks = ctx["preset"].get("brief"), [p for p in ctx["preset"]["picks"] if p["bot_id"] in valid]
        else:
            brief, picks, model = self._decide(task, team, note, ctx if followup else None)
            if model:
                ctx["planner_model"] = model
        assignments = list(ctx.get("assignments", []))
        new = []
        for p in picks[:MAX_ASSIGNMENTS]:
            child = self.tasks.create(bot_id=p["bot_id"], goal=p["goal"], source="coordinator",
                                      requester=task["requester"], requester_scope=task["requester_scope"],
                                      parent_task_id=tid)
            a = {"bot_id": p["bot_id"], "goal": p["goal"], "why": p.get("why", ""), "task_id": child["task_id"],
                 "round": ctx.get("round", 0) + (1 if followup else 0)}
            assignments.append(a)
            new.append(a)
        if brief:
            ctx["brief"] = brief if not followup else ctx.get("brief", brief)
        if followup:
            ctx["round"] = ctx.get("round", 0) + 1
            ctx.setdefault("followups", []).append({"text": note, "to": only})
        ctx["assignments"] = assignments
        self.tasks.save_context(tid, ctx)
        steps = self._steps(assignments, ctx)
        names = "、".join(self.bots.get(a["bot_id"]).display_name for a in new) or "（无需新增分工）"
        msg = (f"追加：{names}" if followup else f"拆成 {len(new)} 项分工，派给 {names}，并行执行")
        self.tasks.coordinate(tid, steps=steps, note=msg, versioned=not followup)
        for i, a in enumerate(new):
            if i and self.runner.background:
                time.sleep(STAGGER_S)  # 错峰启动，减少同时打到模型服务的限流
            self.runner.start(a["task_id"])
        if not new:
            self.runner.start(tid)  # 无新分工：直接重新汇总

    def _steps(self, assignments: list[dict], ctx: dict) -> list[dict]:
        steps = [{"id": "plan", "title": "理解目标并拆分工", "type": "llm", "status": "done"}]
        for a in assignments:
            b = self.bots.get(a["bot_id"])
            steps.append({"id": a["task_id"], "title": f"@{b.display_name}：{a['goal']}", "type": "subtask",
                          "child_task_id": a["task_id"], "bot_id": a["bot_id"], "status": "running"})
        steps.append({"id": "synthesize", "title": "汇总成一份交付", "type": "llm", "status": "pending"})
        return steps

    def _decide(self, task: dict, team: list[dict], note: str | None, prev: dict | None):
        roster = "\n".join(f"- bot_id={m['bot_id']}｜{m['name']}｜{m['summary']}｜能力：{'、'.join(m['capabilities'])}"
                           for m in team)
        system = ("你是一个销售组织的总调度（经理 Agent）。把下达人的目标拆成 1-4 项分工，派给最合适的团队成员并行完成。"
                  "只能从给定成员中选；每项分工写成该成员能直接执行的一句话任务（具体到品牌/型号/区域/时间范围，沿用目标中的信息）。"
                  "不要给不相关的成员派活；能一个人做完就只派一个人。"
                  '只输出 JSON：{"brief": "一句话说明你的分工思路", "assignments": [{"bot_id": "...", "goal": "...", "why": "..."}]}')
        user = f"目标：{task['goal']}\n团队成员：\n{roster}"
        if prev:
            done = "\n".join(f"- {self.bots.get(a['bot_id']).display_name}：{a['goal']}" for a in prev.get("assignments", []))
            user += f"\n已完成的分工：\n{done}\n下达人追加要求：{note}\n只为追加要求补充新的分工；如果只需重新汇总，assignments 返回空数组。"
        elif note:
            user += f"\n下达人补充要求：{note}"
        try:
            out = self.gateway.chat(task["bot_id"], [{"role": "system", "content": system},
                                                     {"role": "user", "content": user}],
                                    task_type="plan", data_class="internal", trace_id=task["trace_id"])
            self.tasks.add_cost(task["task_id"], out["cost_usd"])
            parsed = _json(out["content"])
            valid = {m["bot_id"] for m in team}
            picks = [a for a in (parsed or {}).get("assignments", [])
                     if isinstance(a, dict) and a.get("bot_id") in valid and a.get("goal")]
            if parsed is not None and (picks or prev):
                return parsed.get("brief"), _dedupe(picks), out.get("model")
        except (GatewayError, BudgetExceeded) as e:
            log.warning("coordinator planning model failed: %s", e)
        if prev:
            return None, [], None
        return "按关键词匹配岗位（模型未给出可用分工）", self._fallback(task["goal"] + " " + (note or ""), team), None

    def _fallback(self, goal: str, team: list[dict]) -> list[dict]:
        g = goal.lower()
        by_tpl = {}
        for m in team:
            by_tpl.setdefault(m["template"], m)
        picks = []
        for tpl, words in KEYWORDS.items():
            if tpl in by_tpl and any(w in g for w in words):
                m = by_tpl[tpl]
                picks.append({"bot_id": m["bot_id"], "goal": f"{goal.strip()}（请从{m['name']}的角度：{'、'.join(m['capabilities'][:2])}）",
                              "why": "关键词匹配"})
        if not picks:
            m = by_tpl.get("biz-review") or team[0]
            picks.append({"bot_id": m["bot_id"], "goal": goal.strip(), "why": "默认交给经营复盘"})
        return picks[:MAX_ASSIGNMENTS]

    # ---------- 子任务回调 ----------
    def on_child(self, child: dict) -> None:
        parent_id = child.get("parent_task_id")
        if not parent_id:
            return
        try:
            parent = self.tasks.get(parent_id)
        except TaskError:
            return
        if self.bots.template_of(parent["bot_id"]) != TEMPLATE:
            return
        with self._locks.setdefault(parent_id, threading.RLock()):
            self._child_step(self.tasks.get(parent_id), child)
        if child["status"] in CHILD_DONE:
            self.runner.start(parent_id)

    def _child_step(self, parent: dict, child: dict) -> None:
        steps = parent["steps"]
        for s in steps:
            if s.get("child_task_id") == child["task_id"]:
                s["status"] = {"delivered": "done", "accepted": "done", "failed": "failed",
                               "cancelled": "skipped"}.get(child["status"], "running")
                s["note"] = CHILD_WAITING.get(child["status"])
        self.tasks.child_progress(parent["task_id"], steps)

    # ---------- 汇总 ----------
    def _maybe_finish(self, task: dict, ctx: dict) -> None:
        tid = task["task_id"]
        children = [self.tasks.get(a["task_id"]) for a in ctx.get("assignments", [])]
        if any(c["status"] not in CHILD_DONE for c in children):
            if task["status"] != "awaiting_subtask":
                self.tasks.wait_children(tid)
            return
        ok = [c for c in children if c["status"] in ("delivered", "accepted")]
        idx = len(task["steps"]) - 1
        self.tasks.step(tid, idx, "running")
        if not ok:
            self.tasks.step(tid, idx, "failed", "所有分工都未完成")
            self.tasks.needs_human(tid, "所有分工都失败或被取消，请看各成员的工作流")
            return
        parts = []
        for c in children:
            name = self.bots.get(c["bot_id"]).display_name
            body = "\n\n".join(o["content"] for o in c["outputs"])[:2500] if c in ok else f"（未完成：{c['status_label']}）"
            parts.append(f"### {name}｜分工：{c['goal']}\n{body}")
        notes = "；".join(f["text"] for f in ctx.get("followups", []) if f.get("text"))
        system = ("你是总调度，向下达人汇报团队成果。先给 3-5 条结论（每条一句，带关键数字），再按成员列出要点和下一步建议；"
                  "成员之间结论冲突时指出来；不要编造成员没给出的数据；推断请标注【推断】。用 Markdown，表格可用；全文控制在 600 字以内。")
        user = f"目标：{task['goal']}\n" + (f"下达人追加要求：{notes}\n" if notes else "") + "\n\n".join(parts)
        try:
            out = self.gateway.chat(task["bot_id"], [{"role": "system", "content": system},
                                                     {"role": "user", "content": user}],
                                    task_type="analyze", data_class="internal", trace_id=task["trace_id"])
        except BudgetExceeded as e:
            self.tasks.needs_human(tid, str(e))
            return
        except GatewayError as e:
            self.tasks.step(tid, idx, "failed", str(e))
            self.tasks.fail(tid, f"汇总失败：{e}")
            return
        t = self.tasks.add_cost(tid, out["cost_usd"])
        if t["status"] == "paused":
            return
        self.tasks.step(tid, idx, "done")
        self.memory_note(task, children)
        self.tasks.deliver(tid, [{"step": "synthesize", "kind": "text", "content": out["content"], "model": out.get("model")}])

    def memory_note(self, task: dict, children: list[dict]) -> None:
        try:
            who = "、".join(sorted({self.bots.get(c["bot_id"]).display_name for c in children}))
            self.memory.propose(namespace=self.bots.get(task["bot_id"]).namespace, type="summary",
                                content=f"「{task['goal'][:60]}」分派给 {who}", source=task["trace_id"],
                                bot_id=task["bot_id"], trace_id=task["trace_id"])
        except Exception:  # noqa: BLE001  记忆失败不影响交付
            pass

    # ---------- 追加（群聊里 @ 成员，或交给总调度再分工） ----------
    def followup(self, task_id: str, *, actor_id: str, text: str, bot_id: str | None = None) -> dict:
        task = self.tasks.get(task_id)
        if self.bots.template_of(task["bot_id"]) != TEMPLATE:
            raise TaskError("只有总调度的任务支持追加分工")
        if task["requester"] and task["requester"] != actor_id:
            raise TaskError("只有下达人可以追加")
        if task["status"] in ("cancelled", "failed", "registered", "planning"):
            raise TaskError(f"任务当前为「{task['status_label']}」，不能追加")
        if not text.strip():
            raise TaskError("请写明要追加什么")
        with self._locks.setdefault(task_id, threading.RLock()):
            self._plan(task, task["context"] or {}, text.strip(), followup=True, only=bot_id)
        return self.tasks.get(task_id)


def _json(text: str) -> Any:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def _dedupe(picks: list[dict]) -> list[dict]:
    seen, out = set(), []
    for p in picks:
        key = (p["bot_id"], p["goal"])
        if key not in seen:
            seen.add(key)
            out.append({"bot_id": p["bot_id"], "goal": str(p["goal"])[:300], "why": str(p.get("why", ""))[:200]})
    return out

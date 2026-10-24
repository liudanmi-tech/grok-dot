"""技能执行器（POC 的智能体运行时）。

POC 期间用确定性的“技能步骤”驱动：工具步骤经工具代理执行，写作 / 分析步骤经模型网关调用模型。
这样治理链路（作用域、审批、审计、预算、进度）可以先完整跑通；Hermes 接入后由 HermesRuntime
替换本执行器，Hermes 通过 /internal/* 接口调用同一套工具代理与任务台账（见 hermes_adapter.py）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from ..config import LAGOS, Clock
from ..gateway.service import BudgetExceeded, GatewayError, ModelGateway
from ..memory import MemoryService
from ..orchestrator.tasks import TaskError, TaskService
from ..tools.proxy import ToolProxy
from .registry import BotRegistry, SkillLibrary

log = logging.getLogger(__name__)
TPL = re.compile(r"\{\{\s*([^}|]+?)\s*(\|\s*json)?\s*\}\}")
RUNNABLE = {"running", "rework", "planning", "registered"}


def lookup(path: str, ctx: dict) -> Any:
    cur: Any = ctx
    for part in path.strip().split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def render(value: Any, ctx: dict) -> Any:
    if isinstance(value, str):
        m = TPL.fullmatch(value.strip())
        if m and not m.group(2):
            return lookup(m.group(1), ctx)

        def sub(mm: re.Match) -> str:
            v = lookup(mm.group(1), ctx)
            if mm.group(2):
                return json.dumps(v, ensure_ascii=False, default=str)[:6000]
            return "" if v is None else str(v)
        return TPL.sub(sub, value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


class StepFailed(Exception):
    pass


class Retry(Exception):
    """自动预审退回：带着问题重新起草（最多一次）。"""


class Stop(Exception):
    """任务进入等待（审批、人工、预算）——停止本次执行，等待外部事件恢复。"""


class SkillRunner:
    def __init__(self, *, bots: BotRegistry, skills: SkillLibrary, tasks: TaskService, proxy: ToolProxy,
                 gateway: ModelGateway, memory: MemoryService, clock: Clock):
        self.bots = bots
        self.skills = skills
        self.tasks = tasks
        self.proxy = proxy
        self.gateway = gateway
        self.memory = memory
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self.background = True
        tasks.on_resume = self.start
        tasks.on_retry = self.retry

    # ---------- 入口 ----------
    def start(self, task_id: str) -> None:
        if self.background:
            threading.Thread(target=self.run, args=(task_id,), daemon=True).start()
        else:
            self.run(task_id)

    def retry(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        steps = task["steps"]
        for s in steps:
            if s["status"] == "running":
                s["status"] = "pending"
        self.tasks._update(task_id, steps=steps)
        self.start(task_id)

    def run(self, task_id: str) -> None:
        lock = self._locks.setdefault(task_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return  # 同一任务不并发执行
        try:
            self._run(task_id)
        except Stop:
            pass
        except Exception as e:  # 兜底：任何未预期错误都落到台账上，不让任务悄悄消失
            log.exception("task %s crashed", task_id)
            try:
                self.tasks.fail(task_id, f"执行异常：{e}")
            except TaskError:
                pass
        finally:
            lock.release()

    # ---------- 主流程 ----------
    def _plan_from_skill(self, skill: dict) -> list[dict]:
        out = []
        for s in skill["steps"]:
            out.append({"id": s["id"], "title": s.get("title", s["id"]), "type": s["type"], "tool": s.get("tool"),
                        "est_minutes": s.get("est_minutes", 1), "approval_action": s.get("approval_action", False),
                        "cross_bot": s.get("tool") == "bus.send" and s.get("cross_bot", False)})
        return out

    def _run(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task["status"] not in RUNNABLE:
            return
        skill = self.skills.get(self.bots.get(task["bot_id"]).template, task["skill"])
        if task["status"] in ("registered", "planning"):
            # 运行可复现（23.3）：记录本次使用的技能版本
            self.tasks.audit.append("task.skill", bot_id=task["bot_id"], trace_id=task["trace_id"],
                                    task_id=task_id, versions={"skill": f"{skill['name']}@{skill.get('version')}"})
            by = "requester" if task["status"] == "planning" else "bot"
            task = self.tasks.submit_plan(task_id, self._plan_from_skill(skill), by=by)
            if task["status"] != "running":
                return  # 等待下达人确认
        if task["status"] == "rework":
            steps = task["steps"]
            for s in steps:
                if s["type"] == "llm" or s.get("id") in ("send",):
                    s["status"] = "pending"
            task = self.tasks._update(task_id, steps=steps, status="running")

        ctx = task["context"] or {}
        ctx.setdefault("steps", {})
        ctx["goal"] = task["goal"] + (f"\n补充要求：{ctx['requester_note']}" if ctx.get("requester_note") else "")
        ctx["today"] = self.clock.now().astimezone(LAGOS).date().isoformat()
        outputs = list(task["outputs"]) if task["status"] != "rework" else []

        for i, spec in enumerate(skill["steps"]):
            task = self.tasks.get(task_id)
            if task["status"] != "running":
                return
            st = task["steps"][i] if i < len(task["steps"]) else None
            if st is None or st["status"] in ("done", "skipped"):
                continue
            if "when" in spec and not render(spec["when"], ctx):
                self.tasks.step(task_id, i, "skipped", "条件不满足，跳过")
                continue
            self.tasks.step(task_id, i, "running")
            try:
                result = self._exec(task, spec, ctx)
            except Retry as r:
                if self._retry(task, spec, i, ctx, str(r)):
                    return self._run(task_id)
                return
            except StepFailed as e:
                self.tasks.step(task_id, i, "failed", str(e))
                self.tasks.fail(task_id, f"步骤「{spec.get('title', spec['id'])}」失败：{e}")
                return
            except Stop:
                self.tasks.save_context(task_id, ctx)
                raise
            ctx["steps"][spec["id"]] = {"result": result}
            self.tasks.save_context(task_id, ctx)
            if spec.get("output") and isinstance(result, dict) and result.get("content"):
                outputs.append({"step": spec["id"], "kind": "text", "content": result["content"],
                                "model": result.get("model")})
            self.tasks.step(task_id, i, "done")

        self._remember(task, ctx)
        self.tasks.deliver(task_id, outputs)

    # ---------- 步骤执行 ----------
    def _tool(self, task: dict, tool: str, args: dict) -> dict:
        res = self.proxy.call(task["bot_id"], tool, args, trace_id=task["trace_id"], task_id=task["task_id"],
                              requester=task["requester"], requester_scope=task["requester_scope"])
        return res

    def _exec(self, task: dict, spec: dict, ctx: dict) -> Any:
        kind = spec["type"]
        if kind == "tool":
            res = self._tool(task, spec["tool"], render(spec.get("args", {}), ctx))
            return self._handle(task, spec, res)
        if kind == "llm":
            return self._llm(task, spec, ctx)
        if kind == "foreach":
            items = render(spec["items"], ctx) or []
            results = []
            for item in items:
                local = {**ctx, "item": item}
                one = {}
                for sub in spec["do"]:
                    res = self._tool(task, sub["tool"], render(sub.get("args", {}), local))
                    out = self._handle(task, sub, res, wait=False)
                    local[sub["id"]] = {"result": out}
                    one[sub["id"]] = out
                results.append(one)
            return {"items": results, "count": len(results)}
        raise StepFailed(f"未知步骤类型 {kind}")

    def _handle(self, task: dict, spec: dict, res: dict, wait: bool | None = None) -> Any:
        status = res["status"]
        if status == "ok":
            return res["result"]
        if status in ("pending_approval", "returned"):
            self.tasks.link_approval(task["task_id"], res["approval_id"])
            if status == "returned":
                msg = "; ".join(i["msg"] for i in res["review"]["issues"])
                if spec.get("retry_from"):
                    raise Retry(msg)
                raise StepFailed(f"自动预审退回：{msg}")
            if wait if wait is not None else spec.get("wait_approval", False):
                self.tasks.await_approval(task["task_id"], res["approval_id"])
                raise Stop()
            return {"approval_id": res["approval_id"], "status": "pending_approval"}
        if status == "handoff":
            self.tasks.needs_human(task["task_id"], f"{spec.get('tool')} 须由人完成")
            raise Stop()
        raise StepFailed(res.get("error", status))

    def _llm(self, task: dict, spec: dict, ctx: dict) -> dict:
        bot = self.bots.get(task["bot_id"])
        namespaces = [bot.namespace] + ([f"user/{task['requester']}"] if task["requester"] else [])
        mems = self.memory.recall(namespaces, ctx["goal"], k=5)
        system = render(spec.get("system", ""), ctx)
        if mems:
            system += "\n\n可参考的偏好与方法（来自记忆，不是业务事实）：\n" + "\n".join(f"- {m['content']}" for m in mems)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": render(spec.get("prompt", "{{goal}}"), ctx)}]
        try:
            out = self.gateway.chat(task["bot_id"], messages, task_type=spec.get("task_type"),
                                    data_class=spec.get("data_class", "internal"), trace_id=task["trace_id"])
        except BudgetExceeded as e:
            self.tasks.needs_human(task["task_id"], str(e))
            raise Stop() from e
        except GatewayError as e:
            raise StepFailed(str(e)) from e
        t = self.tasks.add_cost(task["task_id"], out["cost_usd"])
        if t["status"] == "paused":
            raise Stop()
        return out

    def _retry(self, task: dict, spec: dict, index: int, ctx: dict, msg: str) -> bool:
        tid = task["task_id"]
        if ctx.get("_review_retries", 0) >= 1:
            self.tasks.step(tid, index, "failed", f"自动预审再次退回：{msg}")
            bot = self.bots.get(task["bot_id"])
            if "dealer-msg.handoff_to_human" in bot.tools:
                self._tool(task, "dealer-msg.handoff_to_human", {"reason": f"自动预审两次未通过：{msg}"[:300]})
            self.tasks.needs_human(tid, f"自动预审两次未通过：{msg}")
            return False
        ctx["_review_retries"] = 1
        ctx["review_feedback"] = (f"YOUR PREVIOUS DRAFT WAS REJECTED BY THE COMPLIANCE REVIEW: {msg}. "
                                  "Rewrite it to fix these problems.")
        steps = self.tasks.get(tid)["steps"]
        start = next(k for k, s in enumerate(steps) if s["id"] == spec["retry_from"])
        for s in steps[start:index + 1]:
            s["status"] = "pending"
        self.tasks._update(tid, steps=steps, notes=self.tasks._note(self.tasks.get(tid), f"自动预审退回，重新起草：{msg}"))
        self.tasks.save_context(tid, ctx)
        return True

    def _remember(self, task: dict, ctx: dict) -> None:
        """交付后写一条摘要类记忆（不含业务数字；被拦截则忽略）。"""
        try:
            self.memory.propose(namespace=self.bots.get(task["bot_id"]).namespace, type="summary",
                                content=f"完成任务「{task['goal'][:80]}」，使用技能 {task['skill']}",
                                source=task["trace_id"], bot_id=task["bot_id"], trace_id=task["trace_id"])
        except Exception:  # noqa: BLE001 记忆写入失败不影响交付
            pass

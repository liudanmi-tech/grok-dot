"""目标与节奏：KPI 树 → 周分解 → 每日晨会派活 → 白天干活与群广播 → 晚会点评 → 日报上报。

- 领导（国家经理）设 KPI 并指定主管；主管认领、选 bot 小队。
- 结果导向：KPI 实际值来自销量数据，不取 bot 的自我汇报。
- 晨会由总调度主持：看 KPI 缺口、昨日结果、主管昨晚的点评，逐个点评 bot 并派出今日任务（复用总调度的分工执行）。
- 晚会由真人主管主持：系统先备好每个 bot 的完成情况，主管打分点评后，日报自动发给领导；点评写入 bot 记忆，次日晨会采纳。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from ..config import LAGOS, iso
from ..db import dumps, loads
from ..gateway.service import BudgetExceeded, GatewayError
from .coordinator import TEMPLATE as CHIEF
from .tasks import TaskError

log = logging.getLogger(__name__)

STANDUP_AT = (7, 30)
REVIEW_AT = (18, 0)
BASELINE_DAYS = 14
RATINGS = {"good": "超出预期", "ok": "符合预期", "behind": "落后"}

# 模型不可用时的兜底周目标（按岗位）
DEFAULT_OBJECTIVES = {
    "market-intel": ("盯住竞品在目标区域的调价与新品，找出我们可以抢的价格带", "每日竞品变化清单"),
    "price-sentinel": ("找出拖累 KPI 的滞销型号，提出促销 / 调价方案并推动审批", "提交并获批的方案数"),
    "biz-review": ("每天跟踪 KPI 达成与各型号贡献，指出缺口最大的型号与门店", "达成率日报"),
    "content-factory": ("为本周主推型号产出多语种促销文案与经销商话术", "可用素材数"),
    "voice-of-customer": ("找出影响成交的售后问题与口碑风险，给出应对建议", "Top 问题清单"),
    "promoter-coach": ("把本周主推卖点与竞品话术教给促销员", "培训覆盖门店数"),
}


class RhythmError(Exception):
    pass


class Rhythm:
    def __init__(self, *, db, audit, clock, bots, tasks, coordinator, gateway, sales, memory, outbox, users):
        self.db = db
        self.audit = audit
        self.clock = clock
        self.bots = bots
        self.tasks = tasks
        self.coordinator = coordinator
        self.gateway = gateway
        self.sales = sales
        self.memory = memory
        self.outbox = outbox
        self.users = users
        self.background = True
        self._busy: set[str] = set()
        self._lock = threading.Lock()
        self._kpi_locks: dict[str, threading.Lock] = {}
        tasks.listeners.append(self.on_task_status)

    # ================= 时间 =================
    def today(self) -> date:
        return self.clock.now().astimezone(LAGOS).date()

    def _at(self, d: date, hm: tuple[int, int]) -> datetime:
        return datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=LAGOS)

    # ================= KPI =================
    def _row(self, kpi_id: str) -> dict:
        r = self.db.one("SELECT * FROM kpis WHERE kpi_id=?", [kpi_id])
        if not r:
            raise RhythmError("KPI 不存在")
        r["filter"] = loads(r["filter"], {})
        r["squad"] = loads(r["squad"], [])
        return r

    def list_kpis(self) -> list[dict]:
        rows = self.db.query("SELECT kpi_id FROM kpis ORDER BY created_at")
        return [self._row(r["kpi_id"]) for r in rows]

    def create_kpi(self, *, actor: str, title: str, brand: list[str] | None, region: list[str] | None,
                   uplift_pct: float, weeks: int = 4, owner: str | None = None, parent_id: str | None = None) -> dict:
        if not title.strip():
            raise RhythmError("请写 KPI 名称")
        if not 0 < uplift_pct <= 100:
            raise RhythmError("提升幅度应在 0–100% 之间")
        if owner and owner not in self.users:
            raise RhythmError("指定的负责人不存在")
        start = self.today()
        end = start + timedelta(days=7 * max(1, min(weeks, 12)) - 1)
        base = self.sales.daily_units(region=region, brand=brand, start=(start - timedelta(days=BASELINE_DAYS)).isoformat(),
                                      end=(start - timedelta(days=1)).isoformat())
        baseline = sum(base.values()) / max(1, len(base))
        kpi_id = "kpi_" + uuid.uuid4().hex[:8]
        self.db.execute(
            "INSERT INTO kpis(kpi_id, parent_id, title, metric, filter, baseline_daily, uplift_pct, start_date, end_date,"
            " owner, squad, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [kpi_id, parent_id, title.strip(), "sell_through_units", dumps({"brand": brand, "region": region}),
             round(baseline, 2), uplift_pct, start.isoformat(), end.isoformat(), owner, dumps([]),
             "assigned" if owner else "open", actor, iso(self.clock.now())])
        self.audit.append("kpi.create", actor=actor, kpi_id=kpi_id, title=title, owner=owner, uplift_pct=uplift_pct)
        if owner:
            self.outbox.post(recipient=f"user:{owner}", kind="message", card={
                "title": f"你有一个新 KPI：{title}", "body": f"由 {self._uname(actor)} 下达，请认领并组建 bot 小队。"})
        return self.kpi(kpi_id)

    def claim(self, kpi_id: str, *, actor: str, squad: list[str]) -> dict:
        k = self._row(kpi_id)
        if k["owner"] and k["owner"] != actor:
            raise RhythmError("只有被指定的负责人可以认领")
        team = {m["bot_id"] for m in self.coordinator.roster()}
        squad = [b for b in dict.fromkeys(squad) if b in team]
        if not squad:
            raise RhythmError("请至少选择一名可派内部任务的 bot")
        self.db.execute("UPDATE kpis SET owner=?, squad=?, status='active', claimed_at=? WHERE kpi_id=?",
                        [actor, dumps(squad), iso(self.clock.now()), kpi_id])
        self.audit.append("kpi.claim", actor=actor, kpi_id=kpi_id, squad=squad)
        names = "、".join(self.bots.get(b).display_name for b in squad)
        self.say(kpi_id, actor, "user", "human", f"我认领了这个 KPI，小队成员：{names}。总调度先做第一周分解。")
        self._spawn(f"week:{kpi_id}", lambda: self.ensure_week(kpi_id))
        return self.kpi(kpi_id)

    def set_squad(self, kpi_id: str, *, actor: str, squad: list[str]) -> dict:
        k = self._row(kpi_id)
        if k["owner"] != actor:
            raise RhythmError("只有负责人可以调整小队")
        return self.claim(kpi_id, actor=actor, squad=squad)

    def scope_of(self, k: dict) -> dict:
        scope: dict = {"country": ["NG"]}
        for dim in ("region", "brand"):
            if k["filter"].get(dim):
                scope[dim] = k["filter"][dim]
        return scope

    # ---------- 进度（结果导向：来自销量数据） ----------
    def progress(self, k: dict) -> dict:
        start, end = date.fromisoformat(k["start_date"]), date.fromisoformat(k["end_date"])
        days_total = (end - start).days + 1
        as_of = min(self.today() - timedelta(days=1), end)
        daily = self.sales.daily_units(region=k["filter"].get("region"), brand=k["filter"].get("brand"),
                                       start=start.isoformat(), end=as_of.isoformat()) if as_of >= start else {}
        target_daily = k["baseline_daily"] * (1 + k["uplift_pct"] / 100)
        elapsed = max(0, (as_of - start).days + 1)
        actual = sum(daily.values())
        expected = target_daily * elapsed
        pace = actual / expected if expected else None
        rag = None if pace is None else "green" if pace >= 1 else "amber" if pace >= 0.95 else "red"
        series = [{"date": (start + timedelta(days=i)).isoformat(),
                   "actual": daily.get((start + timedelta(days=i)).isoformat()),
                   "target": round(target_daily), "baseline": round(k["baseline_daily"])} for i in range(days_total)]
        lift = (actual / elapsed / k["baseline_daily"] - 1) * 100 if elapsed and k["baseline_daily"] else None
        return {"target_total": round(target_daily * days_total), "target_daily": round(target_daily),
                "actual": actual, "expected": round(expected), "pace": round(pace, 3) if pace else pace, "rag": rag,
                "lift_pct": round(lift, 1) if lift is not None else None, "days_elapsed": elapsed,
                "days_total": days_total, "as_of": as_of.isoformat() if elapsed else None, "series": series,
                "gap": round(expected - actual) if elapsed else 0}

    def kpi(self, kpi_id: str, *, detail: bool = False) -> dict:
        k = self._row(kpi_id)
        k["progress"] = self.progress(k)
        k["owner_name"] = self._uname(k["owner"])
        k["created_by_name"] = self._uname(k["created_by"])
        k["week_no"] = self.week_no(k, self.today())
        today = self.today().isoformat()
        k["today"] = {m["kind"]: m["status"] for m in self.db.query(
            "SELECT kind, status FROM meetings WHERE kpi_id=? AND day=?", [kpi_id, today])}
        last = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='review' AND status='done'"
                           " ORDER BY day DESC LIMIT 1", [kpi_id])
        k["last_report"] = (loads(last["data"], {}).get("report") if last else None)
        if detail:
            k["weeks"] = self.weeks(kpi_id)
            k["meetings"] = self.meetings(kpi_id)
            k["channel"] = self.channel(kpi_id)
            k["children"] = [self.kpi(c["kpi_id"]) for c in self.db.query(
                "SELECT kpi_id FROM kpis WHERE parent_id=? ORDER BY created_at", [kpi_id])]
        return k

    def tree(self) -> list[dict]:
        roots = self.db.query("SELECT kpi_id FROM kpis WHERE parent_id IS NULL ORDER BY created_at")
        out = []
        for r in roots:
            k = self.kpi(r["kpi_id"])
            k["children"] = [self.kpi(c["kpi_id"]) for c in self.db.query(
                "SELECT kpi_id FROM kpis WHERE parent_id=? ORDER BY created_at", [r["kpi_id"]])]
            out.append(k)
        return out

    # ================= 周分解 =================
    def week_no(self, k: dict, d: date) -> int:
        return max(1, (d - date.fromisoformat(k["start_date"])).days // 7 + 1)

    def weeks(self, kpi_id: str) -> list[dict]:
        rows = self.db.query("SELECT * FROM kpi_weeks WHERE kpi_id=? ORDER BY week_no", [kpi_id])
        for r in rows:
            r["items"] = loads(r["items"], [])
        return rows

    def week(self, kpi_id: str, week_no: int) -> dict | None:
        r = self.db.one("SELECT * FROM kpi_weeks WHERE kpi_id=? AND week_no=?", [kpi_id, week_no])
        if r:
            r["items"] = loads(r["items"], [])
        return r

    def ensure_week(self, kpi_id: str, d: date | None = None) -> dict:
        k = self._row(kpi_id)
        d = d or self.today()
        n = self.week_no(k, d)
        w = self.week(kpi_id, n)
        if w:
            return w
        start = date.fromisoformat(k["start_date"]) + timedelta(days=7 * (n - 1))
        end = min(start + timedelta(days=6), date.fromisoformat(k["end_date"]))
        prog = self.progress(k)
        remaining_days = max(1, prog["days_total"] - prog["days_elapsed"])
        week_target = (prog["target_total"] - prog["actual"]) / remaining_days * ((end - start).days + 1)
        brief, items = self._plan_week(k, n, prog, round(week_target))
        self.db.execute("INSERT INTO kpi_weeks(kpi_id, week_no, start_date, end_date, target_units, items, brief, status,"
                        " created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        [kpi_id, n, start.isoformat(), end.isoformat(), round(week_target), dumps(items), brief,
                         "draft", iso(self.clock.now())])
        self.audit.append("kpi.week_plan", kpi_id=kpi_id, week_no=n, items=len(items))
        lines = "\n".join(f"- @{self.bots.get(i['bot_id']).display_name}：{i['objective']}（衡量：{i['measure']}）" for i in items)
        self.say(kpi_id, self._chief(), "bot", "week",
                 f"第 {n} 周分解（{start.isoformat()} ~ {end.isoformat()}，本周需完成 {round(week_target)} 台）：{brief or ''}\n{lines}")
        return self.week(kpi_id, n)

    def confirm_week(self, kpi_id: str, week_no: int, *, actor: str, items: list[dict] | None = None) -> dict:
        k = self._row(kpi_id)
        if k["owner"] != actor:
            raise RhythmError("只有负责人可以确认周计划")
        if items is not None:
            valid = set(k["squad"])
            items = [i for i in items if i.get("bot_id") in valid and i.get("objective")]
            self.db.execute("UPDATE kpi_weeks SET items=? WHERE kpi_id=? AND week_no=?", [dumps(items), kpi_id, week_no])
        self.db.execute("UPDATE kpi_weeks SET status='confirmed', confirmed_by=? WHERE kpi_id=? AND week_no=?",
                        [actor, kpi_id, week_no])
        self.say(kpi_id, actor, "user", "human", f"第 {week_no} 周计划已确认。")
        return self.week(kpi_id, week_no)

    def _plan_week(self, k: dict, n: int, prog: dict, week_target: int) -> tuple[str | None, list[dict]]:
        squad = [self._member(b) for b in k["squad"]]
        prev = self.week(k["kpi_id"], n - 1)
        system = ("你是总调度，负责把 KPI 分解成本周每个 bot 的周目标。每个 bot 一条，目标要直接服务于 KPI（销量提升），"
                  "写清楚本周要交付什么、怎么衡量。只输出 JSON："
                  '{"brief": "一句话本周打法", "items": [{"bot_id": "...", "objective": "...", "measure": "..."}]}')
        user = (f"KPI：{k['title']}；范围：{self._filter_text(k)}；基线日均 {round(k['baseline_daily'])} 台，目标提升 {k['uplift_pct']}%。\n"
                f"当前进度：{self._prog_text(k, prog)}"
                f"本周（第 {n} 周）需完成 {week_target} 台。\n小队：\n"
                + "\n".join(f"- bot_id={m['bot_id']}｜{m['name']}｜{m['summary']}" for m in squad)
                + (f"\n上周计划：{json.dumps(prev['items'], ensure_ascii=False)}" if prev else ""))
        parsed = self._ask(k, system, user)
        items = []
        if parsed:
            items = [{"bot_id": i["bot_id"], "objective": str(i["objective"])[:200], "measure": str(i.get("measure", ""))[:80]}
                     for i in parsed.get("items", []) if isinstance(i, dict) and i.get("bot_id") in k["squad"] and i.get("objective")]
        have = {i["bot_id"] for i in items}
        for b in k["squad"]:  # 兜底：每个成员都有周目标
            if b not in have:
                obj, measure = DEFAULT_OBJECTIVES.get(self.bots.get(b).template, ("配合 KPI 推进本岗位工作", "交付物"))
                items.append({"bot_id": b, "objective": obj, "measure": measure})
        return (parsed or {}).get("brief"), items

    # ================= 晨会 =================
    def _klock(self, kpi_id: str) -> threading.Lock:
        with self._lock:
            return self._kpi_locks.setdefault(kpi_id, threading.Lock())

    def standup(self, kpi_id: str, *, actor: str = "system", force: bool = False) -> dict:
        with self._klock(kpi_id):  # 同一 KPI 的会议串行：自动触发与手动点击不会开出两场
            return self._standup(kpi_id, actor=actor, force=force)

    def _standup(self, kpi_id: str, *, actor: str, force: bool) -> dict:
        k = self._row(kpi_id)
        if k["status"] != "active":
            raise RhythmError("KPI 还没有被认领")
        day = self.today()
        existing = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='standup' AND day=?", [kpi_id, day.isoformat()])
        if existing and not force:
            return self._meeting(existing)
        if not self._chief():
            raise RhythmError("总调度不在岗，无法开晨会")
        week = self.ensure_week(kpi_id, day)
        prog = self.progress(k)
        yday = self._yesterday(kpi_id, day)
        squad = [self._member(b) for b in k["squad"]]
        system = ("你是总调度，正在主持销售小队的晨会。先用两三句话说清 KPI 进度和缺口，然后逐个点评每个 bot 昨天的表现"
                  "（结合主管昨晚的点评，好的肯定、差的指出问题），再给每个 bot 派今天的一项具体任务（直接服务于本周目标与 KPI 缺口，"
                  "写成该 bot 可以直接执行的一句话，带品牌/型号/区域）。只输出 JSON："
                  '{"summary": "...", "critiques": [{"bot_id": "...", "rating": "good|ok|behind", "comment": "..."}], '
                  '"assignments": [{"bot_id": "...", "goal": "..."}]}')
        user = (f"KPI：{k['title']}（{self._filter_text(k)}），第 {self.week_no(k, day)} 周，今天 {day.isoformat()}。\n"
                f"进度：{self._prog_text(k, prog)}\n"
                f"本周目标：{json.dumps(week['items'], ensure_ascii=False)}\n"
                f"昨天各 bot 结果：{json.dumps(yday['results'], ensure_ascii=False)}\n"
                f"主管昨晚点评与明日调整：{json.dumps(yday['review'], ensure_ascii=False)}\n小队：\n"
                + "\n".join(f"- bot_id={m['bot_id']}｜{m['name']}" for m in squad))
        parsed = self._ask(k, system, user) or {}
        valid = set(k["squad"])
        critiques = [{"bot_id": c["bot_id"], "rating": c.get("rating") if c.get("rating") in RATINGS else "ok",
                      "comment": str(c.get("comment", ""))[:300]}
                     for c in parsed.get("critiques", []) if isinstance(c, dict) and c.get("bot_id") in valid]
        assignments = [{"bot_id": a["bot_id"], "goal": str(a["goal"])[:300], "why": "晨会派活"}
                       for a in parsed.get("assignments", []) if isinstance(a, dict) and a.get("bot_id") in valid and a.get("goal")]
        if not critiques:
            critiques = [{"bot_id": r["bot_id"], "rating": "good" if r["status"] in ("delivered", "accepted") else "behind",
                          "comment": "昨天按时交付。" if r["status"] in ("delivered", "accepted") else f"昨天任务{r['status_label']}，今天要跟上。"}
                         for r in yday["results"]]
        if not assignments:
            assignments = [{"bot_id": i["bot_id"], "goal": f"[{k['title']}] {i['objective']}：今天推进一步，给出可量化的结果",
                            "why": "按周目标"} for i in week["items"]]
        summary = parsed.get("summary") or (f"KPI 已完成 {prog['actual']} / 应完成 {prog['expected']} 台，"
                                            f"缺口 {max(0, prog['gap'])} 台。今天按周计划推进。")
        meeting_id = "mtg_" + uuid.uuid4().hex[:8]
        mission = self.tasks.create(bot_id=self._chief(), goal=f"晨会 {day.isoformat()} · {k['title']}", source="standup",
                                    requester=k["owner"], requester_scope=self.scope_of(k))
        self.tasks.save_context(mission["task_id"], {"kpi_id": kpi_id, "meeting_id": meeting_id,
                                                     "preset": {"brief": summary, "picks": assignments[:6]}})
        data = {"summary": summary, "critiques": critiques, "assignments": assignments, "progress": _brief_prog(prog),
                "week_no": self.week_no(k, day)}
        now = iso(self.clock.now())
        if existing:
            self.db.execute("DELETE FROM meetings WHERE meeting_id=?", [existing["meeting_id"]])
        self.db.execute("INSERT INTO meetings(meeting_id, kpi_id, kind, day, status, data, mission_task_id, host, created_at,"
                        " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [meeting_id, kpi_id, "standup", day.isoformat(), "done", dumps(data), mission["task_id"],
                         self._chief(), now, now])
        self.audit.append("meeting.standup", bot_id=self._chief(), kpi_id=kpi_id, meeting_id=meeting_id,
                          assignments=len(assignments), actor=actor)
        chief = self._chief()
        self.say(kpi_id, chief, "bot", "standup", f"☀️ 晨会 {day.isoformat()}：{summary}", ref=meeting_id)
        for c in critiques:
            self.say(kpi_id, chief, "bot", "critique",
                     f"@{self.bots.get(c['bot_id']).display_name}（{RATINGS[c['rating']]}）{c['comment']}", ref=c["bot_id"])
        for a in assignments[:6]:
            self.say(kpi_id, chief, "bot", "assign", f"@{self.bots.get(a['bot_id']).display_name} 今日任务：{a['goal']}",
                     ref=a["bot_id"])
        self.coordinator.runner.start(mission["task_id"])
        return self._meeting(self.db.one("SELECT * FROM meetings WHERE meeting_id=?", [meeting_id]))

    def _yesterday(self, kpi_id: str, day: date) -> dict:
        prev = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='standup' AND day<? ORDER BY day DESC LIMIT 1",
                           [kpi_id, day.isoformat()])
        review = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='review' AND day<? ORDER BY day DESC LIMIT 1",
                             [kpi_id, day.isoformat()])
        return {"results": self._results(prev["mission_task_id"]) if prev else [],
                "review": (loads(review["data"], {}).get("final") if review else None)}

    def _results(self, mission_id: str | None) -> list[dict]:
        if not mission_id:
            return []
        out = []
        for c in self.tasks.children(mission_id):
            out.append({"bot_id": c["bot_id"], "name": self.bots.get(c["bot_id"]).display_name, "goal": c["goal"],
                        "status": c["status"], "status_label": c["status_label"], "task_id": c["task_id"],
                        "cost_usd": c["cost_usd"],
                        "output": (c["outputs"][0]["content"][:600] if c["outputs"] else None)})
        return out

    # ================= 晚会 =================
    def prepare_review(self, kpi_id: str, *, actor: str = "system", force: bool = False) -> dict:
        with self._klock(kpi_id):
            return self._prepare_review(kpi_id, actor=actor, force=force)

    def _prepare_review(self, kpi_id: str, *, actor: str, force: bool) -> dict:
        k = self._row(kpi_id)
        day = self.today()
        existing = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='review' AND day=?", [kpi_id, day.isoformat()])
        if existing and (existing["status"] == "done" or not force):
            return self._meeting(existing)
        standup = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='standup' AND day=?", [kpi_id, day.isoformat()])
        results = self._results(standup["mission_task_id"] if standup else None)
        prog = self.progress(k)
        system = ("你是总调度，为主管准备今晚的复盘会材料。逐个 bot 评估今天任务的完成情况和对 KPI 的贡献（客观，基于产出），"
                  "给出建议评级和一句话建议；再列出风险和明天的调整建议。只输出 JSON："
                  '{"summary": "...", "per_bot": [{"bot_id": "...", "suggested": "good|ok|behind", "assessment": "..."}], '
                  '"risks": ["..."], "tomorrow": ["..."]}')
        user = (f"KPI：{k['title']}；进度：{self._prog_text(k, prog)}\n"
                f"今天各 bot 结果：{json.dumps(results, ensure_ascii=False)}")
        parsed = (self._ask(k, system, user) if results else None) or {}
        per_bot = {p["bot_id"]: p for p in parsed.get("per_bot", []) if isinstance(p, dict) and p.get("bot_id")}
        rows = []
        for r in results:
            p = per_bot.get(r["bot_id"], {})
            ok = r["status"] in ("delivered", "accepted")
            rows.append({**r, "suggested": p.get("suggested") if p.get("suggested") in RATINGS else ("ok" if ok else "behind"),
                         "assessment": p.get("assessment") or ("已交付，见产出。" if ok else f"未完成（{r['status_label']}）。")})
        data = {"summary": parsed.get("summary") or f"今天派出 {len(results)} 项任务，"
                f"完成 {sum(1 for r in results if r['status'] in ('delivered', 'accepted'))} 项。",
                "rows": rows, "risks": parsed.get("risks", []), "tomorrow": parsed.get("tomorrow", []),
                "progress": _brief_prog(prog)}
        now = iso(self.clock.now())
        if existing:
            self.db.execute("UPDATE meetings SET data=?, updated_at=? WHERE meeting_id=?", [dumps(data), now, existing["meeting_id"]])
            mid = existing["meeting_id"]
        else:
            mid = "mtg_" + uuid.uuid4().hex[:8]
            self.db.execute("INSERT INTO meetings(meeting_id, kpi_id, kind, day, status, data, host, created_at, updated_at)"
                            " VALUES (?,?,?,?,?,?,?,?,?)", [mid, kpi_id, "review", day.isoformat(), "draft", dumps(data),
                                                           k["owner"], now, now])
            self.say(kpi_id, self._chief(), "bot", "review",
                     f"🌙 今晚复盘材料已备好：{data['summary']} 等 {self._uname(k['owner'])} 主持点评。", ref=mid)
            self.outbox.post(recipient=f"user:{k['owner']}", kind="message",
                             card={"title": f"晚会材料已备好：{k['title']}", "body": data["summary"]})
        return self._meeting(self.db.one("SELECT * FROM meetings WHERE meeting_id=?", [mid]))

    def submit_review(self, kpi_id: str, *, actor: str, ratings: dict[str, dict], notes: str = "",
                      adjustments: str = "") -> dict:
        k = self._row(kpi_id)
        if k["owner"] != actor:
            raise RhythmError("只有负责人可以主持晚会")
        m = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='review' AND day=?", [kpi_id, self.today().isoformat()])
        if not m:
            m = self.db.one("SELECT * FROM meetings WHERE meeting_id=?", [self.prepare_review(kpi_id)["meeting_id"]])
        if m["status"] == "done":
            raise RhythmError("今晚的复盘已经提交")
        data = loads(m["data"], {})
        final = []
        for r in data.get("rows", []):
            g = ratings.get(r["bot_id"]) or {}
            score = g.get("score") if g.get("score") in RATINGS else r["suggested"]
            final.append({"bot_id": r["bot_id"], "name": r["name"], "score": score, "comment": (g.get("comment") or "").strip()})
            self.say(kpi_id, actor, "user", "rating", f"@{r['name']}：{RATINGS[score]}" +
                     (f"。{g['comment'].strip()}" if g.get("comment") else ""), ref=r["bot_id"])
            if g.get("comment"):
                try:  # 主管点评沉淀为 bot 的工作方法记忆，次日晨会与执行时召回
                    self.memory.propose(namespace=self.bots.get(r["bot_id"]).namespace, type="method",
                                        content=f"主管点评：{g['comment'].strip()[:200]}", source=m["meeting_id"],
                                        bot_id=r["bot_id"], trace_id=None)
                except Exception:  # noqa: BLE001
                    pass
        prog = self.progress(k)
        report = {"day": m["day"], "kpi": k["title"], "owner": self._uname(actor), "progress": _brief_prog(prog),
                  "done": sum(1 for r in data.get("rows", []) if r["status"] in ("delivered", "accepted")),
                  "total": len(data.get("rows", [])), "ratings": final, "notes": notes.strip(),
                  "tomorrow": adjustments.strip() or "；".join(data.get("tomorrow", [])), "risks": data.get("risks", [])}
        data.update({"final": {"ratings": final, "notes": notes.strip(), "adjustments": adjustments.strip()}, "report": report})
        self.db.execute("UPDATE meetings SET status='done', data=?, updated_at=? WHERE meeting_id=?",
                        [dumps(data), iso(self.clock.now()), m["meeting_id"]])
        self.audit.append("meeting.review", kpi_id=kpi_id, meeting_id=m["meeting_id"], actor=actor)
        leader = self._leader(k)
        text = (f"📊 日报 {m['day']} · {k['title']}：完成 {report['done']}/{report['total']} 项任务；"
                f"KPI {prog['actual']}/{prog['expected']} 台（节奏 {'—' if prog['pace'] is None else str(round(prog['pace'] * 100)) + '%'}）。明日：{report['tomorrow'] or '按计划推进'}")
        self.say(kpi_id, actor, "user", "report", text, ref=m["meeting_id"])
        if leader:
            self.outbox.post(recipient=f"user:{leader}", kind="message", card={"title": f"日报：{k['title']}", "body": text})
        return self._meeting(self.db.one("SELECT * FROM meetings WHERE meeting_id=?", [m["meeting_id"]]))

    # ================= 会议、群 =================
    def _meeting(self, r: dict) -> dict:
        r = dict(r)
        r["data"] = loads(r["data"], {})
        if r.get("mission_task_id"):
            r["results"] = self._results(r["mission_task_id"])
            try:
                t = self.tasks.get(r["mission_task_id"])
                r["mission"] = {"task_id": t["task_id"], "status": t["status"], "status_label": t["status_label"],
                                "progress_done": t["progress_done"], "progress_total": t["progress_total"],
                                "output": t["outputs"][0]["content"] if t["outputs"] else None,
                                "has_card": bool(t["outputs"] and t["outputs"][0].get("card"))}
            except TaskError:
                r["mission"] = None
        return r

    def meetings(self, kpi_id: str, limit: int = 20) -> list[dict]:
        rows = self.db.query("SELECT * FROM meetings WHERE kpi_id=? ORDER BY day DESC, created_at DESC LIMIT ?", [kpi_id, limit])
        return [self._meeting(r) for r in rows]

    def say(self, kpi_id: str, author: str | None, author_kind: str, kind: str, text: str, ref: str | None = None) -> None:
        self.db.execute("INSERT INTO channel_messages(kpi_id, ts, author, author_kind, kind, text, ref) VALUES (?,?,?,?,?,?,?)",
                        [kpi_id, iso(self.clock.now()), author, author_kind, kind, text, ref])

    def channel(self, kpi_id: str, limit: int = 80) -> list[dict]:
        rows = self.db.query("SELECT * FROM channel_messages WHERE kpi_id=? ORDER BY id DESC LIMIT ?", [kpi_id, limit])
        for r in rows:
            r["author_name"] = (self.bots.get(r["author"]).display_name if r["author_kind"] == "bot" and r["author"]
                                else self._uname(r["author"]))
        return list(reversed(rows))

    def post_human(self, kpi_id: str, *, actor: str, text: str) -> None:
        self._row(kpi_id)
        if not text.strip():
            raise RhythmError("消息不能为空")
        self.say(kpi_id, actor, "user", "human", text.strip()[:1000])

    # ---------- 白天：bot 进度自动广播到群 ----------
    def on_task_status(self, task: dict, before: str) -> None:
        if task["status"] == before:
            return
        ctx = task.get("context") or {}
        kpi_id = ctx.get("kpi_id")
        if not kpi_id and task.get("parent_task_id"):
            try:
                kpi_id = (self.tasks.get(task["parent_task_id"])["context"] or {}).get("kpi_id")
            except TaskError:
                return
        if not kpi_id:
            return
        name = self.bots.get(task["bot_id"]).display_name
        goal = task["goal"][:60]
        msg = {"running": f"开始干活：{goal}" if before == "registered" else None,
               "delivered": f"✅ 完成：{goal}", "needs_human": f"🙋 需要人帮忙：{goal}",
               "awaiting_approval": f"⏳ 提交审批，等待批准：{goal}", "failed": f"❌ 没完成：{goal}"}.get(task["status"])
        if task["bot_id"] == self._chief() and task["status"] == "delivered":
            msg = "📋 今日任务全部结束，汇总已交付，晚会见。"
        if msg:
            self.say(kpi_id, task["bot_id"], "bot", "progress", msg, ref=task["task_id"])

    # ================= 定时 =================
    def tick(self) -> None:
        now = self.clock.now().astimezone(LAGOS)
        day = now.date()
        for k in self.list_kpis():
            if k["status"] != "active" or day > date.fromisoformat(k["end_date"]):
                continue
            kid = k["kpi_id"]
            if now >= self._at(day, STANDUP_AT) and not self.db.one(
                    "SELECT 1 FROM meetings WHERE kpi_id=? AND kind='standup' AND day=?", [kid, day.isoformat()]):
                self._spawn(f"standup:{kid}", lambda kid=kid: self.standup(kid))
            elif now >= self._at(day, REVIEW_AT) and not self.db.one(
                    "SELECT 1 FROM meetings WHERE kpi_id=? AND kind='review' AND day=?", [kid, day.isoformat()]):
                self._spawn(f"review:{kid}", lambda kid=kid: self.prepare_review(kid))

    def _spawn(self, key: str, fn) -> None:
        with self._lock:
            if key in self._busy:
                return
            self._busy.add(key)

        def run():
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("rhythm job %s failed", key)
            finally:
                with self._lock:
                    self._busy.discard(key)
        if self.background:
            threading.Thread(target=run, daemon=True, name=key).start()
        else:
            run()

    # ================= 工具 =================
    def _ask(self, k: dict, system: str, user: str) -> dict | None:
        try:
            out = self.gateway.chat(self._chief(), [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                    task_type="plan", data_class="internal")
        except (GatewayError, BudgetExceeded) as e:
            log.warning("rhythm model call failed: %s", e)
            return None
        m = re.search(r"\{.*\}", out["content"] or "", re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except ValueError:
            return None

    def _chief(self) -> str:
        return self.bots.resolve(CHIEF)

    def _member(self, bot_id: str) -> dict:
        b = self.bots.get(bot_id)
        meta = self.bots.meta.get(b.template, {})
        return {"bot_id": b.id, "name": b.display_name, "summary": meta.get("summary", "")}

    def _uname(self, uid: str | None) -> str | None:
        return (self.users.get(uid) or {}).get("name", uid) if uid else None

    def _leader(self, k: dict) -> str | None:
        if k["parent_id"]:
            return self._row(k["parent_id"])["owner"] or self._row(k["parent_id"])["created_by"]
        return k["created_by"] if k["created_by"] != k["owner"] else None

    def _prog_text(self, k: dict, prog: dict) -> str:
        if not prog["days_elapsed"]:
            return (f"今天是 KPI 周期第一天，销量数据次日才出（数据截至昨天），暂无达成数据，不要判断为落后；"
                    f"基线日均 {round(k['baseline_daily'])} 台，目标日均 {prog['target_daily']} 台。")
        return (f"已完成 {prog['actual']} 台，应完成 {prog['expected']} 台，达成节奏 {round(prog['pace'] * 100)}%，"
                f"日均较基线 {prog['lift_pct']}%（数据截至 {prog['as_of']}）。")

    def _filter_text(self, k: dict) -> str:
        f = k["filter"]
        return " · ".join(["/".join(f.get("brand") or ["全部品牌"]), "/".join(f.get("region") or ["全国"])])


def _brief_prog(p: dict) -> dict:
    return {k: p[k] for k in ("actual", "expected", "pace", "rag", "lift_pct", "gap", "target_total", "days_elapsed",
                              "days_total")}

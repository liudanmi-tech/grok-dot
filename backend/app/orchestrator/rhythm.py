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

from ..config import MARKETS, iso, market, tz_of
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
    "channel-expansion": ("激活目标区域的沉睡门店，排好每周拜访路线并推动补货与陈列", "动销门店数"),
    "channel-incentive": ("设计拉动主推型号的经销商与促销员激励方案并推动审批", "获批方案数 / 预期增量"),
    "ecommerce-live": ("排好每周电商直播并优化主推型号的店铺转化", "直播场次与电商销量"),
    "campaign-planner": ("为最近的大促档期做活动方案（让利、分期、以旧换新、节奏）", "方案落地与档期销量"),
}


# 每个岗位默认的可量化周指标（模型没给出时兜底）：(指标名, 目标值, 单位)
DEFAULT_METRICS = {
    "market-intel": ("竞品动态清单", 5, "份"), "price-sentinel": ("获批促销 / 调价方案", 2, "个"),
    "biz-review": ("达成率日报", 5, "份"), "content-factory": ("可用营销素材", 10, "套"),
    "voice-of-customer": ("Top 问题清单", 1, "份"), "promoter-coach": ("培训覆盖门店", 20, "家"),
    "channel-expansion": ("激活沉睡门店", 10, "家"), "channel-incentive": ("获批激励方案", 1, "个"),
    "ecommerce-live": ("电商直播", 6, "场"), "campaign-planner": ("档期活动方案", 1, "份"),
}


# 奖金：激励真人员工用 AI 冲目标。按时达到里程碑即解锁（负责人领取），按时主持晚会自动奖励
BONUS_POOL = {"NG": 600000, "ID": 6000000}
REVIEW_REWARD = {"NG": 5000, "ID": 50000}
REVIEW_DEADLINE_HOUR = 21
CURRENCY_SIGN = {"NGN": "₦", "IDR": "Rp"}


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
        return self.clock.now().astimezone(tz_of(self.market(None))).date()

    # 每个 KPI 按自己市场的时区过日子（拉各斯 / 雅加达各自 07:30 晨会、18:00 晚会）
    def market(self, k: dict | None) -> dict:
        countries = ((k or {}).get("filter") or {}).get("country") or [None]
        return market(countries[0])

    def day_of(self, k: dict | None) -> date:
        return self.clock.now().astimezone(tz_of(self.market(k))).date()

    def _at(self, d: date, hm: tuple[int, int], k: dict | None = None) -> datetime:
        return datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=tz_of(self.market(k)))

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
                   uplift_pct: float, weeks: int = 4, owner: str | None = None, parent_id: str | None = None,
                   country: str | None = None) -> dict:
        if not title.strip():
            raise RhythmError("请写 KPI 名称")
        if not 0 < uplift_pct <= 100:
            raise RhythmError("提升幅度应在 0–100% 之间")
        if owner and owner not in self.users:
            raise RhythmError("指定的负责人不存在")
        country = country or "NG"
        if country not in MARKETS:
            raise RhythmError(f"未知市场 {country}")
        bad = [r for r in (region or []) if r not in MARKETS[country]["regions"]]
        if bad:
            raise RhythmError(f"{MARKETS[country]['name']}没有区域 {'、'.join(bad)}")
        start = self.day_of({"filter": {"country": [country]}})
        end = start + timedelta(days=7 * max(1, min(weeks, 12)) - 1)
        base = self.sales.daily_units(region=region, brand=brand, start=(start - timedelta(days=BASELINE_DAYS)).isoformat(),
                                      end=(start - timedelta(days=1)).isoformat(), country=[country])
        baseline = sum(base.values()) / max(1, len(base))
        kpi_id = "kpi_" + uuid.uuid4().hex[:8]
        self.db.execute(
            "INSERT INTO kpis(kpi_id, parent_id, title, metric, filter, baseline_daily, uplift_pct, start_date, end_date,"
            " owner, squad, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [kpi_id, parent_id, title.strip(), "sell_through_units", dumps({"brand": brand, "region": region, "country": [country]}),
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
        code = self.market(k)["code"]
        # 只能选同一市场的成员（bot 的国家作用域要覆盖 KPI 所在国家）
        team = {m["bot_id"] for m in self.coordinator.roster() if code in (m["scope"].get("country") or [code])}
        squad = [b for b in dict.fromkeys(squad) if b in team]
        if not squad:
            raise RhythmError(f"请至少选择一名{self.market(k)['name']}市场、可派内部任务的 bot")
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
        scope: dict = {"country": k["filter"].get("country") or ["NG"]}
        for dim in ("region", "brand"):
            if k["filter"].get(dim):
                scope[dim] = k["filter"][dim]
        return scope

    # ---------- 进度（结果导向：来自销量数据） ----------
    def progress(self, k: dict) -> dict:
        start, end = date.fromisoformat(k["start_date"]), date.fromisoformat(k["end_date"])
        days_total = (end - start).days + 1
        as_of = min(self.day_of(k) - timedelta(days=1), end)
        daily = self.sales.daily_units(region=k["filter"].get("region"), brand=k["filter"].get("brand"),
                                       country=k["filter"].get("country") or ["NG"],
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
        k["bonus"] = self.bonus(k, k["progress"])
        k["owner_name"] = self._uname(k["owner"])
        k["created_by_name"] = self._uname(k["created_by"])
        k["week_no"] = self.week_no(k, self.day_of(k))
        k["market"] = {x: self.market(k)[x] for x in ("code", "name", "city", "currency", "tz")}
        today = self.day_of(k).isoformat()
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
        d = d or self.day_of(k)
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
                  "写清楚本周要交付什么、怎么衡量，并给出一个可量化的周 KPI 指标（指标名、目标数值、单位）。只输出 JSON："
                  '{"brief": "一句话本周打法", "items": [{"bot_id": "...", "objective": "...", "measure": "...", '
                  '"kpi": {"name": "指标名", "target": 10, "unit": "家"}}]}')
        user = (f"KPI：{k['title']}；范围：{self._filter_text(k)}；基线日均 {round(k['baseline_daily'])} 台，目标提升 {k['uplift_pct']}%。\n"
                f"当前进度：{self._prog_text(k, prog)}"
                f"本周（第 {n} 周）需完成 {week_target} 台。\n小队：\n"
                + "\n".join(f"- bot_id={m['bot_id']}｜{m['name']}｜{m['summary']}" for m in squad)
                + (f"\n上周计划：{json.dumps(prev['items'], ensure_ascii=False)}" if prev else ""))
        parsed = self._ask(k, system, user)
        items = []
        if parsed:
            items = [{"bot_id": i["bot_id"], "objective": str(i["objective"])[:200], "measure": str(i.get("measure", ""))[:80],
                      "kpi": self._metric(i.get("kpi"), i["bot_id"])}
                     for i in parsed.get("items", []) if isinstance(i, dict) and i.get("bot_id") in k["squad"] and i.get("objective")]
        have = {i["bot_id"] for i in items}
        for b in k["squad"]:  # 兜底：每个成员都有周目标
            if b not in have:
                obj, measure = DEFAULT_OBJECTIVES.get(self.bots.get(b).template, ("配合 KPI 推进本岗位工作", "交付物"))
                items.append({"bot_id": b, "objective": obj, "measure": measure, "kpi": self._metric(None, b)})
        return (parsed or {}).get("brief"), items

    def _metric(self, raw, bot_id: str) -> dict:
        """规范化每个 bot 的周 KPI 指标；模型没给或给错时用岗位默认指标。"""
        if isinstance(raw, dict) and raw.get("name"):
            try:
                target = float(raw.get("target"))
                return {"name": str(raw["name"])[:30], "target": int(target) if target == int(target) else target,
                        "unit": str(raw.get("unit") or "")[:6], "source": "plan"}
            except (TypeError, ValueError):
                pass
        name, target, unit = DEFAULT_METRICS.get(self.bots.get(bot_id).template, ("交付物", 3, "份"))
        return {"name": name, "target": target, "unit": unit, "source": "default"}

    def plan_week(self, kpi_id: str, week_no: int, *, actor: str) -> dict:
        """主管手动为某一周生成分解（含未来的周），形成里程碑。"""
        k = self._row(kpi_id)
        if k["status"] != "active":
            raise RhythmError("KPI 还没有被认领")
        total = (date.fromisoformat(k["end_date"]) - date.fromisoformat(k["start_date"])).days // 7 + 1
        if not 1 <= week_no <= total:
            raise RhythmError(f"周次超出范围（1–{total}）")
        d = date.fromisoformat(k["start_date"]) + timedelta(days=7 * (week_no - 1))
        w = self.ensure_week(kpi_id, d)
        self.audit.append("kpi.week_plan_manual", actor=actor, kpi_id=kpi_id, week_no=week_no)
        return w

    # ---------- 奖金（游戏化即时反馈） ----------
    def _bonus_state(self, kpi_id: str) -> dict:
        r = self.db.one("SELECT v FROM kv WHERE k=?", [f"bonus.{kpi_id}"])
        return json.loads(r["v"]) if r else {}

    def _bonus_save(self, kpi_id: str, st: dict) -> None:
        with self.db.lock:
            self.db.execute("DELETE FROM kv WHERE k=?", [f"bonus.{kpi_id}"])
            self.db.execute("INSERT INTO kv(k, v) VALUES (?, ?)", [f"bonus.{kpi_id}", json.dumps(st, ensure_ascii=False)])

    def bonus(self, k: dict, prog: dict | None = None) -> dict:
        prog = prog or self.progress(k)
        mk = self.market(k)
        st = self._bonus_state(k["kpi_id"])
        pool = st.get("pool") or BONUS_POOL.get(mk["code"], 600000)
        start, end = date.fromisoformat(k["start_date"]), date.fromisoformat(k["end_date"])
        today = self.day_of(k)
        n_weeks = (end - start).days // 7 + 1
        td = prog["target_daily"]
        # 里程碑：开门红（前 3 天达到 5%）→ 每周累计目标 → 最终 100%
        specs = [("开门红", start + timedelta(days=2), prog["target_total"] * 0.05, 0.05)]
        for n in range(1, n_weeks):
            we = start + timedelta(days=7 * n - 1)
            specs.append((f"第 {n} 周里程碑", we, td * ((we - start).days + 1), 0.60 / max(1, n_weeks - 1)))
        specs.append(("最终目标", end, prog["target_total"], 0.35))
        cum, running = {}, 0
        for s in prog["series"]:
            running += s["actual"] or 0
            cum[s["date"]] = running
        claims = st.get("claims", {})
        tiers = []
        for i, (label, deadline, threshold, share) in enumerate(specs):
            threshold = round(threshold)
            reached = next((d for d, v in cum.items() if v >= threshold and d <= deadline.isoformat()), None)
            if str(i) in claims:
                status = "claimed"
            elif reached:
                status = "unlocked"
            elif today > deadline:
                status = "missed"
            else:
                status = "pending"
            tiers.append({"no": i, "label": label, "deadline": deadline.isoformat(), "threshold": threshold,
                          "pos_pct": round(min(100, 100 * threshold / max(1, prog["target_total"])), 2),
                          "amount": round(pool * share, -2), "status": status, "reached_on": reached,
                          "remaining": max(0, threshold - prog["actual"]), "days_left": (deadline - today).days,
                          "claimed_at": (claims.get(str(i)) or {}).get("at"), "claimed_by": (claims.get(str(i)) or {}).get("by")})
        reward = REVIEW_REWARD.get(mk["code"], 5000)
        on_time = 0
        tz = tz_of(mk)
        for m in self.db.query("SELECT day, updated_at FROM meetings WHERE kpi_id=? AND kind='review' AND status='done'", [k["kpi_id"]]):
            t = datetime.fromisoformat(m["updated_at"]).astimezone(tz)
            if t.date().isoformat() == m["day"] and t.hour < REVIEW_DEADLINE_HOUR:
                on_time += 1
        claimed = sum(t["amount"] for t in tiers if t["status"] == "claimed")
        nxt = next((t for t in tiers if t["status"] in ("pending", "unlocked")), None)
        return {"currency": mk["currency"], "sign": CURRENCY_SIGN.get(mk["currency"], ""), "pool": pool, "tiers": tiers,
                "claimed": claimed, "actions": {"review_on_time": on_time, "reward_each": reward, "total": on_time * reward},
                "earned": claimed + on_time * reward, "unclaimed": sum(t["amount"] for t in tiers if t["status"] == "unlocked"),
                "next": nxt}

    def claim_bonus(self, kpi_id: str, tier_no: int, *, actor: str) -> dict:
        k = self._row(kpi_id)
        if k["owner"] != actor:
            raise RhythmError("只有 KPI 负责人可以领取奖金")
        b = self.bonus(k)
        tier = next((t for t in b["tiers"] if t["no"] == tier_no), None)
        if not tier:
            raise RhythmError("没有这一档奖金")
        if tier["status"] != "unlocked":
            raise RhythmError({"claimed": "这一档已经领取过了", "missed": "已过截止时间，未解锁",
                               "pending": "还没有达到解锁条件"}[tier["status"]])
        st = self._bonus_state(kpi_id)
        st.setdefault("claims", {})[str(tier_no)] = {"at": iso(self.clock.now()), "by": actor}
        self._bonus_save(kpi_id, st)
        amount = f"{b['sign']}{tier['amount']:,.0f}"
        self.audit.append("kpi.bonus_claim", actor=actor, kpi_id=kpi_id, tier=tier_no, amount=tier["amount"], currency=b["currency"])
        self.say(kpi_id, actor, "user", "bonus", f"🎉 解锁奖金「{tier['label']}」{amount}！提前完成 {tier['threshold']:,} 台目标，继续冲下一档。")
        return self.bonus(self._row(kpi_id))

    def set_bonus_pool(self, kpi_id: str, pool: float, *, actor: str) -> dict:
        k = self._row(kpi_id)
        if not 0 < pool <= 1e10:
            raise RhythmError("奖金池金额不合理")
        st = self._bonus_state(kpi_id)
        st["pool"] = pool
        self._bonus_save(kpi_id, st)
        self.audit.append("kpi.bonus_pool", actor=actor, kpi_id=kpi_id, pool=pool)
        return self.bonus(k)

    # ---------- 目标分解脑图：KPI → 周里程碑 → 每个 bot 的周指标 → 具体任务 ----------
    def decomposition(self, kpi_id: str) -> dict:
        k = self.kpi(kpi_id)
        p = k["progress"]
        start, end = date.fromisoformat(k["start_date"]), date.fromisoformat(k["end_date"])
        today = self.day_of(k)
        as_of = p["as_of"]
        daily = {s["date"]: s["actual"] for s in p["series"]}
        weeks = {w["week_no"]: w for w in self.weeks(kpi_id)}
        standups = self.db.query("SELECT day, mission_task_id FROM meetings WHERE kpi_id=? AND kind='standup' ORDER BY day", [kpi_id])
        tasks_by_week: dict[int, dict[str, list]] = {}
        for m in standups:
            wn = self.week_no(k, date.fromisoformat(m["day"]))
            for r in self._results(m["mission_task_id"]):
                tasks_by_week.setdefault(wn, {}).setdefault(r["bot_id"], []).append(
                    {"task_id": r["task_id"], "goal": r["goal"], "status": r["status"], "status_label": r["status_label"],
                     "day": m["day"], "chat_id": m["mission_task_id"]})
        n_weeks = (end - start).days // 7 + 1
        milestones = []
        for n in range(1, n_weeks + 1):
            ws = start + timedelta(days=7 * (n - 1))
            we = min(ws + timedelta(days=6), end)
            days = [(ws + timedelta(days=i)).isoformat() for i in range((we - ws).days + 1)]
            actual = sum(daily.get(d) or 0 for d in days if as_of and d <= as_of)
            w = weeks.get(n)
            target = w["target_units"] if w else round(p["target_daily"] * len(days))
            elapsed = sum(1 for d in days if as_of and d <= as_of)
            expected = target * elapsed / len(days)
            if today > we:
                status = "done" if actual >= target else "missed"
            elif today >= ws:
                status = "current" if not elapsed or actual >= expected * 0.95 else "behind"
            else:
                status = "upcoming"
            agents = []
            for item in (w["items"] if w else []):
                ts = tasks_by_week.get(n, {}).get(item["bot_id"], [])
                b = self.bots.get(item["bot_id"])
                meta = self.bots.meta.get(b.template, {})
                agents.append({"bot_id": b.id, "name": b.display_name, "color": meta.get("color", "#64748b"),
                               "objective": item["objective"], "measure": item.get("measure"),
                               "kpi": item.get("kpi") or self._metric(None, b.id), "tasks": ts,
                               "done": sum(1 for t in ts if t["status"] in ("delivered", "accepted")), "total": len(ts)})
            milestones.append({"week_no": n, "start": ws.isoformat(), "end": we.isoformat(), "target_units": target,
                               "actual_units": actual, "expected_units": round(expected), "status": status,
                               "planned": bool(w), "plan_status": w["status"] if w else None,
                               "brief": w["brief"] if w else None, "agents": agents})
        return {"kpi": {"kpi_id": k["kpi_id"], "title": k["title"], "owner": k["owner"], "owner_name": k["owner_name"],
                        "status": k["status"], "start": k["start_date"], "end": k["end_date"], "market": k["market"],
                        "progress": {x: p[x] for x in ("actual", "expected", "target_total", "pace", "rag", "lift_pct",
                                                        "days_elapsed", "days_total")}},
                "milestones": milestones, "current_week": self.week_no(k, today)}

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
        day = self.day_of(k)
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
                                                     "preset": {"brief": summary, "picks": assignments[:8]}})
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
        for a in assignments[:8]:
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
        day = self.day_of(k)
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
        field = getattr(self, "field", None)
        field_txt = field.text_for_review(self.market(k)["code"], k["filter"].get("region")) if field else ""
        user = (f"KPI：{k['title']}；进度：{self._prog_text(k, prog)}\n"
                + (f"{field_txt}\n" if field_txt else "") +
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
        m = self.db.one("SELECT * FROM meetings WHERE kpi_id=? AND kind='review' AND day=?", [kpi_id, self.day_of(k).isoformat()])
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
        for k in self.list_kpis():
            now = self.clock.now().astimezone(tz_of(self.market(k)))
            day = now.date()
            if k["status"] != "active" or day > date.fromisoformat(k["end_date"]):
                continue
            kid = k["kpi_id"]
            if now >= self._at(day, STANDUP_AT, k) and not self.db.one(
                    "SELECT 1 FROM meetings WHERE kpi_id=? AND kind='standup' AND day=?", [kid, day.isoformat()]):
                self._spawn(f"standup:{kid}", lambda kid=kid: self.standup(kid))
            elif now >= self._at(day, REVIEW_AT, k) and not self.db.one(
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

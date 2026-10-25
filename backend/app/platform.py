"""组装各服务（模块化单体；生产环境可按 3.3 的命名空间拆分部署）。"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .approvals import ApprovalService
from .artifacts import ArtifactStore
from .audit import AuditLog
from .bots.registry import BotRegistry, SkillLibrary
from .bots.runner import SkillRunner
from .channels.outbox import Outbox
from .config import CONFIG_DIR, DATA_DIR, Clock, DemoClock, Settings, load_yaml
from .db import DB
from .gateway.service import ModelGateway
from .memory import MemoryService
from .orchestrator.bus import MessageBus
from .orchestrator.coordinator import Coordinator
from .orchestrator.rhythm import Rhythm
from .skill_packs import SkillPacks
from . import field_reports
from .orchestrator.scheduler import Scheduler
from .orchestrator.tasks import TaskService
from .policy import PolicyEngine
from .channels.whatsapp import LANG_NAMES, WhatsAppGateway
from .tools.connectors import (carlcare_tickets, competitor_watch, dealer_msg, knowledge, platform_tools,
                               pricing_draft, retail_ops, sales_data)
from .tools.proxy import ToolProxy
from .tools.registry import ToolRegistry


class Platform:
    def __init__(self, settings: Settings | None = None, clock: Clock | None = None,
                 config_dir: Path = CONFIG_DIR, data_dir: Path = DATA_DIR, db_path: str | None = None):
        self.settings = settings or Settings()
        self.clock = clock or DemoClock()
        self.db = DB(db_path or self.settings.database_url or self.settings.db_path)

        policy_cfg = load_yaml(config_dir / "policy.yaml")
        routing_cfg = load_yaml(config_dir / "model-routing.yaml")
        self.task_cfg = load_yaml(config_dir / "task-policy.yaml")
        self.users = {u["id"]: u for u in load_yaml(config_dir / "users.yaml")["users"]}

        self.policy = PolicyEngine(policy_cfg)
        self.audit = AuditLog(self.db, self.clock, versions={
            "policy": self.policy.version, "routing": f"model-routing@{routing_cfg.get('version', 0)}",
            "runtime": "skill-runner@poc",
        })
        self.outbox = Outbox(self.db, self.clock)
        self.artifacts = ArtifactStore(self.db, self.clock)
        meta = load_yaml(config_dir / "bot-templates.yaml") if (config_dir / "bot-templates.yaml").exists() else {}
        self.bots = BotRegistry(config_dir / "profiles", self.db, config_dir / "bot-templates.yaml",
                                seed=meta.get("team_seed"), clock=self.clock)
        self.skills = SkillLibrary(config_dir / "skills")
        self.skill_packs = SkillPacks(config_dir / "skill-packs", self.db, self.clock, self.audit)
        self.memory = MemoryService(self.db, self.audit, self.clock)
        self.gateway = ModelGateway(routing_cfg, self.db, self.audit, self.clock)
        self.gateway.template_of = self.bots.template_of
        self.bus = MessageBus(load_yaml(config_dir / "bus-whitelist.yaml"), self.db, self.audit, self.clock,
                              self.artifacts, template_of=self.bots.template_of, resolve=self.bots.resolve)
        self.approvals = ApprovalService(self.db, self.audit, self.clock, self.policy, self.outbox)

        # 工具与连接器
        self.tools = ToolRegistry()
        self.kb = knowledge.KnowledgeBase(data_dir / "knowledge")
        self.kb.attach(self.db, self.clock, self.audit)
        knowledge.register(self.tools, self.kb)
        self.competitor = competitor_watch.CompetitorWatch(self.clock, self.artifacts)
        competitor_watch.register(self.tools, self.competitor)
        self.sales = sales_data.SalesData(self.clock, self.artifacts)
        sales_data.register(self.tools, self.sales)
        self.pricing = pricing_draft.PricingDrafts(self.db, self.clock, self.artifacts, self.kb)
        pricing_draft.register(self.tools, self.pricing)
        carlcare_tickets.register(self.tools, carlcare_tickets.CarlcareTickets(self.clock, self.artifacts))
        retail_ops.register(self.tools, retail_ops.RetailOps(self.db, self.clock, self.artifacts))
        platform_tools.register(self.tools, outbox=self.outbox, bus=self.bus, memory=self.memory,
                                namespace_of=lambda b: self.bots.get(b).namespace)
        self.whatsapp = WhatsAppGateway(self.db, self.audit, self.clock,
                                        load_yaml(config_dir / "dealers.yaml").get("dealers", []),
                                        load_yaml(config_dir / "promoters.yaml").get("promoters", [])
                                        if (config_dir / "promoters.yaml").exists() else [])
        dealer_msg.register(self.tools, dealer_msg.DealerMsg(self.whatsapp, self.kb))
        self.proxy = ToolProxy(self.tools, self.bots, self.policy, self.approvals, self.audit)

        # 任务与执行
        self.tasks = TaskService(self.db, self.audit, self.clock, self.outbox, self.bots, self.task_cfg)
        self.runner = SkillRunner(bots=self.bots, skills=self.skills, tasks=self.tasks, proxy=self.proxy,
                                  gateway=self.gateway, memory=self.memory, clock=self.clock)
        self.runner.skill_packs = self.skill_packs
        self.coordinator = Coordinator(tasks=self.tasks, bots=self.bots, gateway=self.gateway, runner=self.runner,
                                       memory=self.memory)
        self.field = field_reports.FieldReports(db=self.db, audit=self.audit, clock=self.clock, whatsapp=self.whatsapp,
                                                gateway=self.gateway, bots=self.bots)
        field_reports.register(self.tools, self.field)
        self.rhythm = Rhythm(db=self.db, audit=self.audit, clock=self.clock, bots=self.bots, tasks=self.tasks,
                             coordinator=self.coordinator, gateway=self.gateway, sales=self.sales, memory=self.memory,
                             outbox=self.outbox, users=self.users)
        self.rhythm.field = self.field
        self._load_demo_state()
        self.approvals.listeners.append(self.tasks.on_approval)
        self.approvals.listeners.append(self._on_pricing_approval)
        self.whatsapp.on_dealer_message = self._on_dealer_message
        self.whatsapp.on_handoff = self._on_wa_handoff
        self.scheduler = Scheduler(self.db, self.audit, self.clock, self.bots, self.tasks, self.runner)
        self.scheduler.maintenance += [self.approvals.tick, self.tasks.tick, self.memory.expire, self.rhythm.tick]
        self._stop = threading.Event()

    def _on_dealer_message(self, conv: dict, dealer: dict, text: str, lang: str) -> None:
        task = self.tasks.create(
            bot_id="dealer-assistant", goal=f"回复经销商 {dealer['name']}：{text[:80]}", source="whatsapp",
            # 作用域由网关决定：只能看这个经销商、这个会话（13.4）
            requester_scope={"dealer_id": [dealer["dealer_id"]], "conversation_id": [conv["conversation_id"]],
                             "region": [dealer["region"]]},
        )
        self.tasks.save_context(task["task_id"], {
            "inbound": text, "lang": lang, "lang_name": LANG_NAMES.get(lang, "English"),
            "conversation_id": conv["conversation_id"], "dealer_name": dealer["name"], "review_feedback": "",
        })
        self.runner.start(task["task_id"])

    def _on_wa_handoff(self, conv: dict, reason: str) -> None:
        dealer = self.whatsapp.dealer_of(conv) or {}
        self.outbox.post(recipient="role:channel_manager", kind="message", card={
            "title": f"WhatsApp 转人工：{dealer.get('name', conv['phone'])}",
            "body": f"原因：{reason}\n会话：{conv['conversation_id']}，请在控制台 WhatsApp 页接手处理。"})
        self.audit.append("alert", level="P3", kind="whatsapp.handoff", conversation_id=conv["conversation_id"])

    def _on_pricing_approval(self, rec: dict) -> None:
        if rec["tool"] != "pricing-draft.submit_suggestion":
            return
        draft_id = rec["payload"]["args"].get("draft_id")
        state = {"pending": "submitted", "executed": "approved", "rejected": "rejected", "expired": "expired",
                 "returned": "returned", "failed": "failed"}.get(rec["state"])
        if draft_id and state and state != "approved":  # approved 由执行器写入
            self.pricing.mark(draft_id, state, rec["approval_id"])

    # ---------- 演示：时间快进与销量响应（持久化到 kv） ----------
    def _kv(self, k: str, default=None):
        import json
        r = self.db.one("SELECT v FROM kv WHERE k=?", [k])
        return json.loads(r["v"]) if r else default

    def _kv_set(self, k: str, v) -> None:
        import json
        self.db.execute("DELETE FROM kv WHERE k=?", [k])
        self.db.execute("INSERT INTO kv(k, v) VALUES (?, ?)", [k, json.dumps(v, ensure_ascii=False)])

    def _load_demo_state(self) -> None:
        if isinstance(self.clock, DemoClock):
            self.clock.offset_s = float(self._kv("demo.clock_offset_s", 0) or 0)
        self.sales.effects = self._kv("demo.sales_effects", []) or []

    def advance_clock(self, to: str, market_code: str | None = None) -> dict:
        """快进到（按所选市场的当地时间）：standup（07:30 晨会）、review（今天 18:00 晚会）、next_day（明天 07:30）、reset。"""
        from datetime import datetime, timedelta
        from .config import market, tz_of
        if not isinstance(self.clock, DemoClock):
            raise ValueError("当前时钟不支持快进")
        tz = tz_of(market(market_code))
        now = self.clock.now().astimezone(tz)
        if to == "reset":
            self.clock.offset_s = 0
        else:
            d = now.date()
            hm = {"standup": (7, 31), "review": (18, 1), "next_day": (7, 31)}.get(to)
            if not hm:
                raise ValueError(f"未知快进目标 {to}")
            target = datetime(d.year, d.month, d.day, *hm, tzinfo=tz)
            if to == "next_day" or target <= now:
                target += timedelta(days=1)
            self.clock.offset_s += (target - now).total_seconds()
        self._kv_set("demo.clock_offset_s", self.clock.offset_s)
        self.audit.append("demo.clock", to=to, offset_s=round(self.clock.offset_s))
        self.rhythm.tick()
        return self.clock_state(market_code)

    def clock_state(self, market_code: str | None = None) -> dict:
        from .config import MARKETS, market, tz_of
        m = market(market_code)
        now = self.clock.now().astimezone(tz_of(m))
        return {"now": now.isoformat(timespec="minutes"), "offset_days": round(getattr(self.clock, "offset_s", 0) / 86400, 2),
                "phase": "standup" if now.hour < 12 else "work" if now.hour < 18 else "review",
                "market": m["code"], "city": m["city"],
                "markets": [{"code": x["code"], "name": x["name"], "city": x["city"]} for x in MARKETS.values()]}

    # ---------- 演示数据：印尼（雅加达 Infinix 12.12 档期冲量） ----------
    ID_SQUAD = ["market-intel", "price-sentinel", "ecommerce-live", "campaign-planner", "channel-expansion",
                "channel-incentive", "content-factory"]
    ID_KPI_TITLE = "雅加达 Infinix 12.12 档期终端销量提升 20%"

    def seed_indonesia(self, actor: str) -> dict:
        """招募印尼（雅加达）小队，印尼国家经理下达 KPI，雅加达主管认领并开工。幂等：已存在则直接返回。"""
        hired = []
        for tpl in self.ID_SQUAD:
            mine = [b for b in self.bots.list() if b.template == tpl and b.scope.get("country") == ["ID"]
                    and (b.scope.get("region") or ["Jakarta"]) == ["Jakarta"]]
            b = mine[0] if mine else self.bots.hire(tpl, actor=actor, region="Jakarta", country="ID")
            if not mine:
                self.audit.append("bot.hire", bot_id=b.id, actor=actor, template=tpl, scope=b.scope)
            hired.append(b.id)
        k = next((x for x in self.rhythm.list_kpis() if x["title"] == self.ID_KPI_TITLE), None)
        if not k:
            k = self.rhythm.create_kpi(actor="u-cm-indonesia", title=self.ID_KPI_TITLE, brand=["Infinix"],
                                       region=["Jakarta"], uplift_pct=20, weeks=4, owner="u-lead-jakarta", country="ID")
        if k["status"] != "active":
            self.rhythm.claim(k["kpi_id"], actor="u-lead-jakarta", squad=hired)
        return {"kpi_id": k["kpi_id"], "squad": hired}

    # ---------- 后台调度线程 ----------
    def start_background(self) -> None:
        if not self.settings.scheduler_enabled:
            return

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.scheduler.tick()
                except Exception:  # noqa: BLE001
                    import logging
                    logging.getLogger(__name__).exception("scheduler tick failed")
                self._stop.wait(self.settings.scheduler_interval_s)

        threading.Thread(target=loop, daemon=True, name="scheduler").start()

    def stop(self) -> None:
        self._stop.set()
        time.sleep(0)

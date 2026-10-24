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
from .config import CONFIG_DIR, DATA_DIR, Clock, Settings, load_yaml
from .db import DB
from .gateway.service import ModelGateway
from .memory import MemoryService
from .orchestrator.bus import MessageBus
from .orchestrator.coordinator import Coordinator
from .orchestrator.scheduler import Scheduler
from .orchestrator.tasks import TaskService
from .policy import PolicyEngine
from .channels.whatsapp import LANG_NAMES, WhatsAppGateway
from .tools.connectors import (carlcare_tickets, competitor_watch, dealer_msg, knowledge, platform_tools,
                               pricing_draft, sales_data)
from .tools.proxy import ToolProxy
from .tools.registry import ToolRegistry


class Platform:
    def __init__(self, settings: Settings | None = None, clock: Clock | None = None,
                 config_dir: Path = CONFIG_DIR, data_dir: Path = DATA_DIR, db_path: str | None = None):
        self.settings = settings or Settings()
        self.clock = clock or Clock()
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
        self.memory = MemoryService(self.db, self.audit, self.clock)
        self.gateway = ModelGateway(routing_cfg, self.db, self.audit, self.clock)
        self.gateway.template_of = self.bots.template_of
        self.bus = MessageBus(load_yaml(config_dir / "bus-whitelist.yaml"), self.db, self.audit, self.clock,
                              self.artifacts, template_of=self.bots.template_of, resolve=self.bots.resolve)
        self.approvals = ApprovalService(self.db, self.audit, self.clock, self.policy, self.outbox)

        # 工具与连接器
        self.tools = ToolRegistry()
        self.kb = knowledge.KnowledgeBase(data_dir / "knowledge")
        knowledge.register(self.tools, self.kb)
        self.competitor = competitor_watch.CompetitorWatch(self.clock, self.artifacts)
        competitor_watch.register(self.tools, self.competitor)
        self.sales = sales_data.SalesData(self.clock, self.artifacts)
        sales_data.register(self.tools, self.sales)
        self.pricing = pricing_draft.PricingDrafts(self.db, self.clock, self.artifacts, self.kb)
        pricing_draft.register(self.tools, self.pricing)
        carlcare_tickets.register(self.tools, carlcare_tickets.CarlcareTickets(self.clock, self.artifacts))
        platform_tools.register(self.tools, outbox=self.outbox, bus=self.bus, memory=self.memory,
                                namespace_of=lambda b: self.bots.get(b).namespace)
        self.whatsapp = WhatsAppGateway(self.db, self.audit, self.clock,
                                        load_yaml(config_dir / "dealers.yaml").get("dealers", []))
        dealer_msg.register(self.tools, dealer_msg.DealerMsg(self.whatsapp, self.kb))
        self.proxy = ToolProxy(self.tools, self.bots, self.policy, self.approvals, self.audit)

        # 任务与执行
        self.tasks = TaskService(self.db, self.audit, self.clock, self.outbox, self.bots, self.task_cfg)
        self.runner = SkillRunner(bots=self.bots, skills=self.skills, tasks=self.tasks, proxy=self.proxy,
                                  gateway=self.gateway, memory=self.memory, clock=self.clock)
        self.coordinator = Coordinator(tasks=self.tasks, bots=self.bots, gateway=self.gateway, runner=self.runner,
                                       memory=self.memory)
        self.approvals.listeners.append(self.tasks.on_approval)
        self.approvals.listeners.append(self._on_pricing_approval)
        self.whatsapp.on_dealer_message = self._on_dealer_message
        self.whatsapp.on_handoff = self._on_wa_handoff
        self.scheduler = Scheduler(self.db, self.audit, self.clock, self.bots, self.tasks, self.runner)
        self.scheduler.maintenance += [self.approvals.tick, self.tasks.tick, self.memory.expire]
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

"""模型网关：路由、数据分级过滤、预算、降级、熔断、脱敏、计量（技术方案 5.1–5.5）。"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timezone

from ..audit import AuditLog
from ..config import Clock, iso
from ..db import DB
from .providers import MockProvider, OpenAICompatProvider, OpenRouterProvider, ProviderError
from .settings import ModelSettings
from .redact import contains_secret, redact

PROTECTED_BOTS = {"dealer-assistant", "promoter-coach"}  # 全局超预算时仍放行的对外岗位


class BudgetExceeded(Exception):
    pass


class GatewayError(Exception):
    pass


class ModelGateway:
    def __init__(self, cfg: dict, db: DB, audit: AuditLog, clock: Clock):
        self.cfg = cfg
        self.template_of = lambda bot_id: bot_id.split("--")[0]
        self.db = db
        self.audit = audit
        self.clock = clock
        self.catalog = {m["id"]: m for m in cfg["catalog"]}
        self.version = f"model-routing@{cfg.get('version', 0)}"
        self.mock = MockProvider()
        self.settings = ModelSettings(db, audit)
        self.providers = {"mock": self.mock, "openai_compat": OpenAICompatProvider(),
                          "openrouter": OpenRouterProvider(self.settings.get)}
        cb = cfg.get("circuit_breaker", {})
        self.cb_window = cb.get("window_seconds", 300)
        self.cb_rate = cb.get("error_rate", 0.2)
        self.cb_min = cb.get("min_requests", 5)
        self.cb_open = cb.get("open_seconds", 600)
        self._calls: dict[str, deque] = defaultdict(deque)
        self._open_until: dict[str, float] = {}

    # ---------- 路由 ----------
    def primary(self, bot_id: str, task_type: str | None, language: str | None) -> str:
        bot_id = self.template_of(bot_id)
        for route in self.cfg.get("routes", []):
            when = route.get("when", {})
            if all({"task": task_type, "language": language, "bot": bot_id}.get(k) == v for k, v in when.items()):
                return route["use"]
        return self.cfg.get("defaults", {}).get("bots", {}).get(bot_id) or self.cfg["defaults"]["platform"]

    def chain(self, model: str) -> list[str]:
        out = [model]
        for m in self.cfg.get("fallback", {}).get(model, []):
            if m not in out:
                out.append(m)
        return [m for m in out if m in self.catalog]

    def month_spend(self, bot_id: str | None = None) -> float:
        start = self.clock.now().astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        sql, args = "SELECT COALESCE(SUM(cost_usd),0) s FROM usage WHERE ts >= ?", [iso(start)]
        if bot_id:
            sql += " AND bot_id = ?"
            args.append(bot_id)
        return float(self.db.one(sql, args)["s"])

    def plan(self, bot_id: str, task_type: str | None, language: str | None, data_class: str) -> list[str]:
        budgets = self.cfg.get("budgets", {})
        tpl = self.template_of(bot_id)
        if tpl not in PROTECTED_BOTS and self.month_spend() >= budgets.get("global", float("inf")):
            raise BudgetExceeded("全局月度预算已用尽，非对外岗位暂停调用")
        models = self.chain(self.primary(bot_id, task_type, language))
        models = [m for m in models if data_class in self.catalog[m].get("allowed_data", [])]
        if not models:
            raise GatewayError(f"没有允许处理 {data_class} 级数据的模型")
        bot_budget = budgets.get(bot_id, budgets.get(tpl, budgets.get("default")))
        if bot_budget is not None and self.month_spend(bot_id) >= bot_budget:
            economy = [m for m in models if self.catalog[m]["tier"] == "economy"]
            models = economy or models[-1:]
        return models

    # ---------- 熔断 ----------
    def _breaker_open(self, model: str) -> bool:
        return self._open_until.get(model, 0) > time.monotonic()

    def _record(self, model: str, ok: bool) -> None:
        now = time.monotonic()
        q = self._calls[model]
        q.append((now, ok))
        while q and q[0][0] < now - self.cb_window:
            q.popleft()
        errors = sum(1 for _, o in q if not o)
        if len(q) >= self.cb_min and errors / len(q) > self.cb_rate:
            self._open_until[model] = now + self.cb_open
            self.audit.append("alert", level="P2", kind="model.circuit_open", model=model)

    # ---------- 调用 ----------
    def chat(self, bot_id: str, messages: list[dict], *, task_type: str | None = None,
             language: str | None = None, data_class: str = "internal", trace_id: str | None = None) -> dict:
        joined = "\n".join(m.get("content", "") for m in messages)
        if contains_secret(joined):
            self.audit.append("alert", bot_id=bot_id, trace_id=trace_id, level="P1", kind="secret.in_model_input")
            raise GatewayError("模型输入中检测到疑似凭证，已拦截")
        clean, total_hits = [], {}
        for m in messages:
            text, _mapping, hits = redact(m.get("content", ""))
            clean.append({**m, "content": text})
            for k, v in hits.items():
                total_hits[k] = total_hits.get(k, 0) + v
        if total_hits and self.template_of(bot_id) not in PROTECTED_BOTS:
            # 内部岗位本不应接触个人信息
            self.audit.append("alert", bot_id=bot_id, trace_id=trace_id, level="P2",
                              kind="pii.in_internal_bot", hits=total_hits)

        models = self.plan(bot_id, task_type, language, data_class)
        timeout = self.cfg.get("timeouts", {}).get("total_seconds", 120)
        errors: list[str] = []
        first = models[0]
        for model in models:
            if self._breaker_open(model):
                errors.append(f"{model}: 熔断中")
                continue
            entry = self.catalog[model]
            try:
                out = self.providers[entry["provider"]].chat(entry, clean, timeout)
            except ProviderError as e:
                if e.configured:
                    self._record(model, False)
                errors.append(str(e))
                continue
            self._record(model, True)
            price = entry.get("price_per_m", {})
            cost = out.get("cost_usd")  # OpenRouter 返回实际计费；其余按目录单价估算
            if cost is None:
                cost = (out["input_tokens"] * price.get("input", 0) + out["output_tokens"] * price.get("output", 0)) / 1e6
            self.db.execute(
                "INSERT INTO usage(ts, bot_id, model, trace_id, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?,?)",
                (iso(self.clock.now()), bot_id, model, trace_id, out["input_tokens"], out["output_tokens"], cost),
            )
            if model != first:
                self.audit.append("model.fallback", bot_id=bot_id, trace_id=trace_id,
                                  from_model=first, to_model=model, reasons=errors)
            self.audit.append("model.call", bot_id=bot_id, trace_id=trace_id, model=model, task_type=task_type,
                              upstream_model=out.get("upstream_model"),
                              input_tokens=out["input_tokens"], output_tokens=out["output_tokens"],
                              cost_usd=round(cost, 6), redactions=total_hits or None)
            self._check_budget_alert(bot_id)
            return {"content": out["content"], "model": out.get("upstream_model") or model, "fallback_from": first if model != first else None,
                    "input_tokens": out["input_tokens"], "output_tokens": out["output_tokens"], "cost_usd": cost}
        self.audit.append("alert", bot_id=bot_id, trace_id=trace_id, level="P2", kind="model.all_failed", errors=errors)
        raise GatewayError("降级链全部失败：" + "；".join(errors))

    def _check_budget_alert(self, bot_id: str) -> None:
        budgets = self.cfg.get("budgets", {})
        limit = budgets.get(bot_id, budgets.get(self.template_of(bot_id), budgets.get("default")))
        if not limit:
            return
        ratio = self.month_spend(bot_id) / limit
        if ratio >= budgets.get("alert_at", 0.8):
            self.audit.append("alert", bot_id=bot_id, level="P3" if ratio < 1 else "P2",
                              kind="budget.threshold", ratio=round(ratio, 3))

    def usage_summary(self) -> dict:
        start = self.clock.now().astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        rows = self.db.query(
            "SELECT bot_id, model, COUNT(*) calls, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens,"
            " SUM(cost_usd) cost_usd FROM usage WHERE ts >= ? GROUP BY bot_id, model ORDER BY cost_usd DESC",
            [iso(start)],
        )
        return {"month_start": iso(start), "global_budget": self.cfg.get("budgets", {}).get("global"),
                "spent": self.month_spend(), "rows": rows, "budgets": self.cfg.get("budgets", {})}

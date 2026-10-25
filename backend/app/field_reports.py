"""一线日报员：驻店促销员在 WhatsApp 上报当天销量（任意语言、随手写），自动整理成结构化日报。

- 先用规则识别「型号 + 台数」（"Spark 40 x3"、"sold 3 hot 60i"、"jual 2 GT 30"、"卖了 4 台 Pop 9"），
  识别不了再交给模型抽取；仍识别不了就回一句报数格式提示。
- 每条报数立刻回执（今日累计），当天第一条即为打卡；傍晚可列出未报数的促销员并一键提醒。
- 汇总供主管看板、晚会复盘材料与数字员工工具（field-reports.query_summary）使用。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from .config import iso, market, tz_of
from .gateway.service import BudgetExceeded, GatewayError
from .tools.connectors.sales_data import CATALOG
from .tools.registry import Tool, ToolContext, ToolRegistry
from .policy import allows

RECEIPT = {
    "en": "✅ Received, {name}: {items}. Your total today: {total} units. Thank you!",
    "pcm": "✅ We don receive am, {name}: {items}. Your total today na {total}. Well done!",
    "id": "✅ Diterima, {name}: {items}. Total hari ini: {total} unit. Terima kasih!",
}
ASK_FORMAT = {
    "en": "Please report as model + quantity, e.g. \"Spark 40 x3, Pop 9 x2\".",
    "pcm": "Abeg send am like model + how many, e.g. \"Spark x3, Pop 9 x2\".",
    "id": "Mohon laporkan model + jumlah, contoh \"Hot 60i 3, GT 30 2\".",
}
REMIND = {
    "en": "Hi {name}, please send today's sales for {store} (model + quantity). Thank you!",
    "pcm": "{name}, abeg send today sales for {store} (model + how many). Thank you!",
    "id": "Halo {name}, mohon kirim penjualan hari ini untuk {store} (model + jumlah). Terima kasih!",
}
NUM = r"(\d{1,3})"
UNIT = r"(?:\s*(?:台|部|pcs?|units?|buah|x|×|\*))?"


def _aliases(country: str) -> list[tuple[str, tuple]]:
    """型号别名（长的优先匹配）：完整型号、去空格写法、SKU；尼日利亚 Spark 只有一款时也接受简称。"""
    out = []
    for sku, brand, model in CATALOG[country][0]:
        names = {model.lower(), model.lower().replace(" ", ""), sku.lower()}
        if " " in model:
            series, rest = model.split(" ", 1)
            if sum(1 for _, _, m in CATALOG[country][0] if m.lower().startswith(series.lower())) == 1:
                names.add(series.lower())
            if any(c.isdigit() for c in rest) and len(rest) > 2:
                names.add(rest.lower())
        for n in names:
            out.append((n, (sku, brand, model)))
    return sorted(out, key=lambda x: -len(x[0]))


def parse_rules(text: str, country: str) -> list[dict]:
    t = " " + text.lower().replace("，", ",").replace("：", ":") + " "
    found: dict[str, int] = {}
    taken = [False] * len(t)
    meta = {}
    for alias, (sku, brand, model) in _aliases(country):
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", t):
            if any(taken[m.start():m.end()]):
                continue
            after = re.match(r"\s*[:=\-]?\s*" + r"(?:x|×|\*)?\s*" + NUM + UNIT, t[m.end():m.end() + 12])
            # 数字在型号前，中间允许一个修饰词：「3 台 Pop 9」「1 more camon 40」「2 unit GT 30」
            before = re.search(NUM + UNIT + r"\s*(?:台|部)?\s*(?:[a-z]+\s+)?$", t[max(0, m.start() - 16):m.start()])
            n = int(after.group(1)) if after else int(before.group(1)) if before else None
            for i in range(m.start(), m.end()):  # 认出型号就占住这段文字，避免短别名把「Spark 40」里的 40 当台数
                taken[i] = True
            if n is None or not 0 < n < 500:
                continue
            found[sku] = found.get(sku, 0) + n
            meta[sku] = (brand, model)
    return [{"sku": s, "brand": meta[s][0], "model": meta[s][1], "units": u} for s, u in found.items()]


class FieldReports:
    def __init__(self, *, db, audit, clock, whatsapp, gateway, bots):
        self.db, self.audit, self.clock = db, audit, clock
        self.whatsapp, self.gateway, self.bots = whatsapp, gateway, bots
        whatsapp.on_field_report = self.handle

    def _day(self, country: str) -> str:
        return self.clock.now().astimezone(tz_of(market(country))).date().isoformat()

    # ---------- 收到报数 ----------
    def handle(self, conv: dict, promoter: dict, text: str) -> dict:
        country = promoter.get("country", "NG")
        items, source = parse_rules(text, country), "rule"
        if not items and len(text.strip()) >= 4:  # 规则读不懂（如数字用英文单词写）就交给模型
            items, source = self._parse_llm(text, country), "llm"
        lang = promoter.get("lang", "en") if promoter.get("lang") in RECEIPT else "en"
        if not items:
            self._reply(conv, ASK_FORMAT[lang])
            self.audit.append("field_report.unparsed", promoter_id=promoter["promoter_id"], preview=text[:80])
            return {"parsed": 0}
        day, now = self._day(country), iso(self.clock.now())
        for it in items:
            self.db.execute("INSERT INTO field_reports(day, ts, promoter_id, promoter_name, store, region, country, sku, model,"
                            " brand, units, raw, conversation_id, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [day, now, promoter["promoter_id"], promoter["name"], promoter["store"], promoter["region"],
                             country, it["sku"], it["model"], it["brand"], it["units"], text[:500], conv["conversation_id"], source])
        total = self.db.one("SELECT COALESCE(SUM(units),0) n FROM field_reports WHERE promoter_id=? AND day=?",
                            [promoter["promoter_id"], day])["n"]
        self.audit.append("field_report.create", promoter_id=promoter["promoter_id"], store=promoter["store"],
                          units=sum(i["units"] for i in items), source=source)
        lines = ", ".join(f"{i['model']} ×{i['units']}" for i in items)
        self._reply(conv, RECEIPT[lang].format(name=promoter["name"], items=lines, total=total))
        return {"parsed": len(items), "items": items, "total_today": total, "source": source}

    def _parse_llm(self, text: str, country: str) -> list[dict]:
        models = [m for _, _, m in CATALOG[country][0]]
        bot = self.bots.resolve("field-reporter") or self.bots.resolve("chief-of-staff")
        if not bot:
            return []
        try:
            out = self.gateway.chat(bot, [
                {"role": "system", "content": "从促销员的销量上报中抽取型号和台数。只能从给定型号中选，台数为正整数。只输出 JSON："
                                              '{"items": [{"model": "...", "units": 3}]}；没有销量就输出 {"items": []}。'},
                {"role": "user", "content": f"可选型号：{models}\n上报：{text}"}], task_type="classify", data_class="internal")
            m = re.search(r"\{.*\}", out["content"] or "", re.S)
            raw = json.loads(m.group(0)).get("items", []) if m else []
        except (GatewayError, BudgetExceeded, ValueError):
            return []
        by_model = {mdl: (sku, brand) for sku, brand, mdl in CATALOG[country][0]}
        items = []
        for r in raw:
            if isinstance(r, dict) and r.get("model") in by_model and isinstance(r.get("units"), int) and 0 < r["units"] < 500:
                sku, brand = by_model[r["model"]]
                items.append({"sku": sku, "brand": brand, "model": r["model"], "units": r["units"]})
        return items

    def _reply(self, conv: dict, text: str) -> None:
        try:
            self.whatsapp.send(conv["conversation_id"], text, kind="receipt")
        except RuntimeError as e:  # 熔断 / 限流 / 窗口关闭：回执失败不影响入库
            self.audit.append("alert", level="P3", kind="field_report.receipt_failed", reason=str(e))

    # ---------- 汇总 ----------
    def promoters(self, country: str | None = None, region: list[str] | None = None) -> list[dict]:
        return [p for p in self.whatsapp.promoters if (not country or p.get("country") == country)
                and (not region or p.get("region") in region)]

    def summary(self, country: str = "NG", day: str | None = None, region: list[str] | None = None) -> dict:
        day = day or self._day(country)
        sql, args = "SELECT * FROM field_reports WHERE country=? AND day=?", [country, day]
        rows = [r for r in self.db.query(sql + " ORDER BY id DESC", args) if not region or r["region"] in region]
        by_store, by_model = defaultdict(int), defaultdict(int)
        for r in rows:
            by_store[(r["store"], r["promoter_name"])] += r["units"]
            by_model[r["model"]] += r["units"]
        roster = self.promoters(country, region)
        reported = {r["promoter_id"] for r in rows}
        first = {}
        for r in sorted(rows, key=lambda r: r["ts"]):
            first.setdefault(r["promoter_id"], r["ts"])
        return {"day": day, "country": country, "total_units": sum(r["units"] for r in rows), "reports": len(rows),
                "promoters": len(roster), "reported": len(reported & {p["promoter_id"] for p in roster}),
                "missing": [{k: p[k] for k in ("promoter_id", "name", "store", "region")} for p in roster if p["promoter_id"] not in reported],
                "check_in": {pid: ts for pid, ts in first.items()},
                "by_store": sorted([{"store": s, "promoter": n, "units": u} for (s, n), u in by_store.items()], key=lambda x: -x["units"]),
                "by_model": sorted([{"model": m, "units": u} for m, u in by_model.items()], key=lambda x: -x["units"]),
                "rows": rows[:200]}

    def text_for_review(self, country: str, region: list[str] | None) -> str:
        s = self.summary(country, region=region)
        if not s["promoters"]:
            return ""
        top = "、".join(f"{x['store']} {x['units']} 台" for x in s["by_store"][:3]) or "无"
        models = "、".join(f"{x['model']} {x['units']}" for x in s["by_model"][:4]) or "无"
        miss = "、".join(f"{m['name']}（{m['store']}）" for m in s["missing"]) or "无"
        return (f"一线促销员今日上报：{s['reported']}/{s['promoters']} 人已报，合计 {s['total_units']} 台；"
                f"门店前三：{top}；型号：{models}；未报：{miss}。")

    def remind(self, country: str, *, actor: str, region: list[str] | None = None) -> dict:
        sent, failed = [], []
        for m in self.summary(country, region=region)["missing"]:
            p = next(x for x in self.whatsapp.promoters if x["promoter_id"] == m["promoter_id"])
            conv = self.whatsapp.conversation(p["phones"][0])
            lang = p.get("lang", "en") if p.get("lang") in REMIND else "en"
            try:
                self.whatsapp.send(conv["conversation_id"], REMIND[lang].format(name=p["name"], store=p["store"]), kind="reminder")
                sent.append(p["name"])
            except RuntimeError as e:
                failed.append({"name": p["name"], "reason": str(e)})
        self.audit.append("field_report.remind", actor=actor, country=country, sent=sent, failed=len(failed))
        return {"sent": sent, "failed": failed}


def register(reg: ToolRegistry, fr: FieldReports) -> None:
    def query(args: dict, ctx: ToolContext) -> dict:
        countries = [c for c in CATALOG if allows(ctx.scope, "country", c)]
        out = []
        for c in countries:
            region = [r for r in CATALOG[c][1] if allows(ctx.scope, "region", r)]
            s = fr.summary(c, args.get("day"), region if len(region) < len(CATALOG[c][1]) else None)
            out.append({k: s[k] for k in ("day", "country", "total_units", "reported", "promoters", "missing", "by_store", "by_model")})
        return {"summaries": out, "source": "field_reports（促销员 WhatsApp 上报）"}

    reg.register(Tool(name="field-reports.query_summary", action_class="read:channel", handler=query,
                      description="一线促销员当天销量上报汇总：已报 / 未报、门店与型号排行",
                      schema={"type": "object", "additionalProperties": False, "properties": {"day": {"type": "string"}}}))

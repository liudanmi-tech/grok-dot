"""retail-ops 连接器：门店覆盖、电商与直播、大促档期、分期与以旧换新、渠道激励方案（合成数据，按国家隔离）。

供新岗位使用：渠道拓展员、电商直播运营、活动策划、渠道激励策划。
激励方案只能「提交审批」——返利、授信本身属于禁止由智能体执行的动作（policy: rebate:* / credit:*）。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta

from ...artifacts import ArtifactStore
from ...config import LAGOS, Clock, iso
from ...db import DB, dumps
from ...policy import allows
from ..registry import Tool, ToolContext, ToolRegistry
from .sales_data import CATALOG

CHANNELS = {"NG": ["连锁门店", "区域经销商", "夫妻店"], "ID": ["Erafone 连锁", "区域总代", "toko HP 夫妻店"]}
PLATFORMS = {"NG": ["Jumia", "Konga"], "ID": ["Shopee", "Tokopedia", "TikTok Shop", "Lazada"]}
STORE_WORDS = {"NG": ["Ikeja Phones", "Computer Village Hub", "Kano Mobile", "Onitsha Gadgets", "Wuse Tech"],
               "ID": ["Toko Sinar Jaya", "Cell Mandiri", "Maju Ponsel", "Berkah Gadget", "Mega Cellular"]}
# 每年固定的大促档期（月, 日, 名称, 持续天数）
CALENDAR = {
    "NG": [(11, 28, "Black Friday", 4), (12, 15, "Christmas / Detty December", 16), (3, 20, "Ramadan / Sallah", 30)],
    "ID": [(11, 11, "11.11 大促", 1), (12, 12, "12.12 Harbolnas 全国网购日", 1), (12, 20, "年末假期", 12),
           (2, 8, "斋月（Ramadan）", 29), (3, 9, "开斋节（Lebaran）", 7), (7, 10, "开学季", 21)],
}
FINANCING = {
    "NG": [{"partner": "Easybuy", "terms": "首付 30%，3/6 期", "approval_rate": "约 55%"},
           {"partner": "CDcare", "terms": "首付 40%，3 期", "approval_rate": "约 45%"}],
    "ID": [{"partner": "Kredivo", "terms": "0% 3/6 期（品牌贴息 ≤3%）", "approval_rate": "雅加达约 65%"},
           {"partner": "Akulaku", "terms": "0% 3 期，6 期 1.5%/月", "approval_rate": "约 55%"},
           {"partner": "Home Credit", "terms": "门店即时审批，首付 10%", "approval_rate": "约 50%"},
           {"partner": "Indodana", "terms": "0% 3 期", "approval_rate": "约 50%"}],
}
TRADE_IN = {"NG": "暂无以旧换新合作", "ID": "Erafone 与 Shopee 以旧换新频道：旧机估价 + 品牌补贴最高 IDR 200,000（Note / GT 系列）"}


def _h(*parts) -> int:
    return int(hashlib.md5(":".join(map(str, parts)).encode()).hexdigest(), 16)


class RetailOps:
    def __init__(self, db: DB, clock: Clock, artifacts: ArtifactStore):
        self.db = db
        self.clock = clock
        self.artifacts = artifacts

    def _countries(self, ctx: ToolContext) -> list[str]:
        return [c for c in CATALOG if allows(ctx.scope, "country", c)]

    def _today(self) -> date:
        return self.clock.now().astimezone(LAGOS).date()

    # ---------- 门店覆盖 ----------
    def query_coverage(self, args: dict, ctx: ToolContext) -> dict:
        rows, dormant = [], []
        for c in self._countries(ctx):
            for region in CATALOG[c][1]:
                if not allows(ctx.scope, "region", region) or (args.get("region") and region not in args["region"]):
                    continue
                for ch in CHANNELS[c]:
                    total = 40 + _h(c, region, ch) % 260
                    active = int(total * (0.55 + (_h(region, ch, "a") % 35) / 100))
                    if c == "ID" and region == "Jakarta" and "toko" in ch:
                        active = int(total * 0.48)  # 演示：雅加达夫妻店动销门店占比偏低
                    visited = int(active * (0.3 + (_h(region, ch, "v") % 40) / 100))
                    rows.append({"country": c, "region": region, "channel": ch, "outlets": total, "active_14d": active,
                                 "dormant": total - active, "visited_7d": visited,
                                 "active_rate_pct": round(active / total * 100, 1)})
                for i in range(3):
                    name = STORE_WORDS[c][_h(region, i) % 5]
                    dormant.append({"country": c, "region": region, "store": f"{name} #{100 + _h(region, i, 's') % 900}",
                                    "channel": CHANNELS[c][2], "days_since_last_order": 15 + _h(region, i, "d") % 40,
                                    "last_visit_days": 7 + _h(region, i, "lv") % 30})
        dormant.sort(key=lambda r: r["days_since_last_order"], reverse=True)
        ref = self.artifacts.put(ctx.bot_id, "outlet_coverage", rows)
        return {"rows": rows, "dormant_top": dormant[:12], "ref": ref, "as_of": self._today().isoformat(),
                "source": "crm.outlets（合成数据）"}

    # ---------- 电商与直播 ----------
    def query_ecommerce(self, args: dict, ctx: ToolContext) -> dict:
        rows, live = [], []
        for c in self._countries(ctx):
            skus = CATALOG[c][0]
            for p in PLATFORMS[c]:
                for sku, brand, model in skus:
                    if not allows(ctx.scope, "brand", brand) or (args.get("brand") and brand not in args["brand"]):
                        continue
                    units = 50 + _h(c, p, sku) % 900
                    if sku == "HOT60I" and p == "TikTok Shop":
                        units = int(units * 0.6)  # 演示：Hot 60i 在 TikTok Shop 被竞品直播压制
                    rows.append({"country": c, "platform": p, "model": model, "brand": brand, "units_14d": units,
                                 "conversion_pct": round(1 + (_h(p, sku, "cv") % 40) / 10, 1),
                                 "rating": round(4.5 + (_h(p, sku, "r") % 5) / 10, 1)})
                sessions = 4 + _h(c, p, "ls") % 20
                live.append({"country": c, "platform": p, "live_sessions_14d": sessions,
                             "avg_viewers": 300 + _h(c, p, "av") % 3000, "gmv_share_pct": 10 + _h(c, p, "g") % 50,
                             "best_slot": ["12:00–14:00", "19:00–22:00", "21:00–23:00"][_h(c, p, "slot") % 3]})
        ref = self.artifacts.put(ctx.bot_id, "ecommerce", {"rows": rows, "live": live})
        return {"rows": rows, "live": live, "ref": ref, "as_of": self._today().isoformat(),
                "source": "ecommerce.shop_stats（合成数据）"}

    # ---------- 大促档期 ----------
    def query_calendar(self, args: dict, ctx: ToolContext) -> dict:
        today, horizon = self._today(), args.get("days", 120)
        out = []
        for c in self._countries(ctx):
            for m, d, name, dur in CALENDAR[c]:
                for y in (today.year, today.year + 1):
                    start = date(y, m, d)
                    if today - timedelta(days=dur) <= start <= today + timedelta(days=horizon):
                        out.append({"country": c, "name": name, "start": start.isoformat(),
                                    "end": (start + timedelta(days=dur - 1)).isoformat(),
                                    "days_until": (start - today).days})
        out.sort(key=lambda r: r["start"])
        return {"campaigns": out, "as_of": today.isoformat(), "source": "marketing.calendar"}

    # ---------- 分期与以旧换新 ----------
    def query_financing(self, args: dict, ctx: ToolContext) -> dict:
        cs = self._countries(ctx)
        return {"financing": [{"country": c, **f} for c in cs for f in FINANCING[c]],
                "trade_in": {c: TRADE_IN[c] for c in cs}, "source": "partners.financing"}

    # ---------- 渠道激励方案（只提交审批，不直接发放） ----------
    def submit_incentive(self, args: dict, ctx: ToolContext) -> dict:
        pid = "inc_" + uuid.uuid4().hex[:8]
        self.artifacts.put(ctx.bot_id, "incentive_plan", {"plan_id": pid, **args})
        return {"plan_id": pid, "status": "approved_for_execution",
                "note": "方案已获批，由渠道经理在经销商系统中配置执行（智能体不直接发放返利）"}

    def describe_incentive(self, args: dict, ctx: ToolContext) -> dict:
        preview = (f"{args.get('title')}｜对象：{args.get('audience')}｜区域：{'/'.join(args.get('regions', []))}｜"
                   f"预算：{args.get('budget')}｜规则：{args.get('rules')}")
        return {"target": args.get("title"), "preview": preview, "review_text": preview, "evidence": []}


def register(reg: ToolRegistry, ro: RetailOps) -> None:
    reg.register(Tool(name="retail-ops.query_coverage", action_class="read:channel", handler=ro.query_coverage,
                      description="门店覆盖：各区域 × 渠道的门店数、14 天动销门店、沉睡门店、近 7 天拜访；沉睡门店清单",
                      schema={"type": "object", "additionalProperties": False, "properties": {"region": {"type": "array"}}}))
    reg.register(Tool(name="retail-ops.query_ecommerce", action_class="read:ecommerce", handler=ro.query_ecommerce,
                      description="电商旗舰店与直播：平台 × 型号 14 天销量、转化率、评分；直播场次、观看、GMV 占比、最佳时段",
                      schema={"type": "object", "additionalProperties": False, "properties": {"brand": {"type": "array"}}}))
    reg.register(Tool(name="retail-ops.query_calendar", action_class="read:public", handler=ro.query_calendar,
                      description="未来大促档期（按国家）",
                      schema={"type": "object", "additionalProperties": False,
                              "properties": {"days": {"type": "integer", "minimum": 7, "maximum": 400}}}))
    reg.register(Tool(name="retail-ops.query_financing", action_class="read:internal", handler=ro.query_financing,
                      description="分期合作方条款与以旧换新政策",
                      schema={"type": "object", "additionalProperties": False, "properties": {}}))
    reg.register(Tool(name="retail-ops.submit_incentive", action_class="submit:incentive_plan", handler=ro.submit_incentive,
                      describe=ro.describe_incentive, description="提交渠道激励方案（需国家经理审批；不直接发放返利）",
                      schema={"type": "object", "required": ["title", "audience", "rules", "budget"], "additionalProperties": False,
                              "properties": {"title": {"type": "string", "maxLength": 80},
                                             "audience": {"type": "string", "maxLength": 80},
                                             "regions": {"type": "array"}, "rules": {"type": "string", "maxLength": 1200},
                                             "budget": {"type": "string", "maxLength": 60},
                                             "period": {"type": "string", "maxLength": 60}}}))


__all__ = ["RetailOps", "register", "dumps", "iso"]

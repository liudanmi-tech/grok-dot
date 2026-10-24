"""competitor-watch 连接器：竞品公开价格与新品（只读）。

POC 使用示例数据（时间相对“当前时间”生成）；正式版接入采集服务或第三方价格数据。
附带数据源健康度（技术方案 23.5），早报据此列出“今日缺失的来源”。
"""
from __future__ import annotations

from datetime import timedelta

from ...artifacts import ArtifactStore
from ...config import Clock, iso
from ...policy import allows
from ..registry import Tool, ToolContext, ToolRegistry

PRICE_EVENTS = [
    # (几小时前, 国家, 品牌, 型号, 区域, 原价, 现价, 来源)；价格为当地货币
    (6, "NG", "Samsung", "Galaxy A16", "Lagos", 189000, 175000, "example-ecommerce-1"),
    (20, "NG", "Redmi", "Note 14", "NG", 215000, 209000, "example-ecommerce-1"),
    (50, "NG", "Redmi", "A3", "NG", 99000, 95000, "example-ecommerce-2"),
    (130, "NG", "Samsung", "Galaxy A06", "NG", 119000, 112000, "example-brand-site"),
    # 印尼（IDR）：Redmi 15C 在雅加达电商大幅降价、Samsung A07 开学季促销，冲击 Infinix Hot 60i 价格带
    (8, "ID", "Redmi", "15C", "Jakarta", 1699000, 1499000, "example-shopee"),
    (26, "ID", "Samsung", "Galaxy A07", "ID", 1599000, 1499000, "example-tiktok-shop"),
    (60, "ID", "OPPO", "A5i", "ID", 1799000, 1699000, "example-brand-site"),
    (140, "ID", "vivo", "Y19s", "Jawa Timur", 1899000, 1849000, "example-erafone"),
]
LAUNCHES = [
    (30, "NG", "Samsung", "Galaxy A17", "NG", 205000, "6.7\" · 8+128GB · 5000mAh", "example-brand-site"),
    (100, "NG", "Redmi", "13x", "NG", 129000, "6.79\" · 6+128GB · 5030mAh", "example-ecommerce-2"),
    (40, "ID", "Redmi", "Note 15", "ID", 2599000, "6.77\" · 8+256GB · 5500mAh · 33W", "example-brand-site"),
    (90, "ID", "realme", "C85", "ID", 1999000, "6.8\" · 6+128GB · 7000mAh · IP69", "example-shopee"),
]
CURRENCY = {"NG": "NGN", "ID": "IDR"}
SOURCES = {
    "example-ecommerce-1": {"name": "电商平台 A（示例）", "ok": True},
    "example-ecommerce-2": {"name": "电商平台 B（示例）", "ok": True},
    "example-brand-site": {"name": "竞品官网（示例）", "ok": True},
    "example-ecommerce-3": {"name": "电商平台 C（示例）", "ok": False, "reason": "验证码拦截"},
    "example-shopee": {"name": "Shopee 印尼（示例）", "ok": True},
    "example-tiktok-shop": {"name": "TikTok Shop 印尼（示例）", "ok": True},
    "example-erafone": {"name": "Erafone 门店价（示例）", "ok": True},
}


class CompetitorWatch:
    def __init__(self, clock: Clock, artifacts: ArtifactStore):
        self.clock = clock
        self.artifacts = artifacts

    def _health(self) -> list[dict]:
        now = self.clock.now()
        return [{"source": k, "name": v["name"], "ok": v["ok"], "reason": v.get("reason"),
                 "last_success": iso(now - timedelta(hours=1 if v["ok"] else 52))} for k, v in SOURCES.items()]

    def list_price_changes(self, args: dict, ctx: ToolContext) -> dict:
        now = self.clock.now()
        since = args.get("since_hours", 24)
        brands = set(args.get("brand") or [])
        rows = []
        for h, country, brand, model, region, old, new, src in PRICE_EVENTS:
            if h > since or (brands and brand not in brands) or not allows(ctx.scope, "country", country):
                continue
            rows.append({"observed_at": iso(now - timedelta(hours=h)), "country": country, "brand": brand, "model": model,
                         "region": region, "currency": CURRENCY[country], "old_price": old, "new_price": new,
                         "change_pct": round((new - old) / old * 100, 1), "source": src,
                         "source_name": SOURCES[src]["name"]})
        big = any(abs(r["change_pct"]) >= 5 for r in rows)
        ref = self.artifacts.put(ctx.bot_id, "competitor_prices", rows)
        health = self._health()
        return {"changes": rows, "count": len(rows), "big_moves": big, "ref": ref, "as_of": iso(now),
                "source": "competitor-watch", "missing_sources": [h["name"] for h in health if not h["ok"]],
                "note": "示例数据"}

    def list_launches(self, args: dict, ctx: ToolContext) -> dict:
        now = self.clock.now()
        since = args.get("since_hours", 168)
        rows = [{"announced_at": iso(now - timedelta(hours=h)), "country": c, "brand": b, "model": m, "region": r,
                 "currency": CURRENCY[c], "price": p, "spec": s, "source_name": SOURCES[src]["name"]}
                for h, c, b, m, r, p, s, src in LAUNCHES if h <= since and allows(ctx.scope, "country", c)]
        return {"launches": rows, "count": len(rows), "as_of": iso(now), "source": "competitor-watch",
                "note": "示例数据"}

    def source_health(self) -> list[dict]:
        return self._health()


def register(reg: ToolRegistry, cw: CompetitorWatch) -> None:
    common = {"since_hours": {"type": "integer", "minimum": 1}, "brand": {"type": "array"}}
    reg.register(Tool(
        name="competitor-watch.list_price_changes", action_class="read:public", handler=cw.list_price_changes,
        description="汇总竞品价格变化（公开来源）",
        schema={"type": "object", "additionalProperties": False, "properties": common},
    ))
    reg.register(Tool(
        name="competitor-watch.list_launches", action_class="read:public", handler=cw.list_launches,
        description="竞品新品与定价（公开来源）",
        schema={"type": "object", "additionalProperties": False, "properties": common},
    ))

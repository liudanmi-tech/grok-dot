"""carlcare-tickets 连接器：售后工单统计（只读、已去标识化）。

POC 使用确定性的合成数据：Infinix Hot 50 的“充电”问题近 7 天明显上升，用于演示用户声音分析员。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, timedelta

from ...artifacts import ArtifactStore
from ...config import LAGOS, Clock
from ...policy import allows
from ..registry import Tool, ToolContext, ToolRegistry
from .sales_data import REGIONS, SKUS

CATEGORIES = ["屏幕", "电池续航", "充电", "相机", "系统卡顿", "进水"]


class CarlcareTickets:
    def __init__(self, clock: Clock, artifacts: ArtifactStore):
        self.clock = clock
        self.artifacts = artifacts

    def query_tickets(self, args: dict, ctx: ToolContext) -> dict:
        days = min(max(args.get("days", 14), 7), 56)
        as_of = self.clock.now().astimezone(LAGOS).date() - timedelta(days=1)
        half = days // 2
        agg: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])  # [前半段, 后半段]
        for sku, brand, model in SKUS:
            if not allows(ctx.scope, "brand", brand):
                continue
            for region in REGIONS:
                if not allows(ctx.scope, "region", region):
                    continue
                for cat in CATEGORIES:
                    for i in range(days):
                        d: date = as_of - timedelta(days=i)
                        h = int(hashlib.md5(f"{sku}{region}{cat}{d}".encode()).hexdigest(), 16)
                        n = 1 if h % 9 == 0 else 0
                        if sku == "X6873" and cat == "充电" and i < 7:
                            n += 2 if h % 3 else 1  # Infinix Hot 50 充电问题近 7 天上升
                        agg[(model, brand, cat)][0 if i >= half else 1] += n
        rows = []
        for (model, brand, cat), (prev, last) in agg.items():
            if prev + last == 0:
                continue
            rows.append({"model": model, "brand": brand, "category": cat, "tickets": prev + last,
                         "last_half": last, "prev_half": prev,
                         "change_pct": round((last - prev) / prev * 100, 1) if prev else None})
        rows.sort(key=lambda r: r["tickets"], reverse=True)
        ref = self.artifacts.put(ctx.bot_id, "carlcare_tickets", rows[:30])
        return {"rows": rows[:30], "count": len(rows), "days": days, "as_of": as_of.isoformat(), "ref": ref,
                "source": "carlcare.tickets（合成数据，已去标识化）"}


def register(reg: ToolRegistry, ct: CarlcareTickets) -> None:
    reg.register(Tool(
        name="carlcare-tickets.query_tickets", action_class="read:aftersales", handler=ct.query_tickets,
        description="查询售后工单统计（按型号 × 问题类别，已去标识化）",
        schema={"type": "object", "additionalProperties": False,
                "properties": {"days": {"type": "integer", "minimum": 7}}},
    ))

"""sales-data 连接器：出货、终端销量、库存（数据中台只读副本）。

POC 使用确定性的合成数据；其中 TECNO Spark X6851 在 Lagos 近 14 天持续下滑，用于演示哨兵链路。
所有查询按作用域（country / region / brand）在服务端过滤，下达人看不到范围外的行（技术方案 11.6）。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, timedelta

from ...artifacts import ArtifactStore
from ...config import LAGOS, Clock
from ...policy import allows
from ..registry import Tool, ToolContext, ToolRegistry

SKUS = [
    ("X6851", "TECNO", "Spark X6851"),
    ("KL4", "TECNO", "Pop 9"),
    ("CM9", "TECNO", "Camon 40"),
    ("X6873", "Infinix", "Hot 50"),
    ("X6858", "Infinix", "Note 40"),
    ("A70", "itel", "A70"),
]
REGIONS = ["Lagos", "North", "South-West", "South-East", "FCT"]
HISTORY_DAYS = 35


def _base(sku: str, region: str) -> int:
    h = int(hashlib.md5(f"{sku}:{region}".encode()).hexdigest(), 16)
    return 40 + h % 160


def _noise(sku: str, region: str, d: date) -> float:
    h = int(hashlib.md5(f"{sku}:{region}:{d.isoformat()}".encode()).hexdigest(), 16)
    return 0.9 + (h % 21) / 100  # 0.90 – 1.10


class SalesData:
    def __init__(self, clock: Clock, artifacts: ArtifactStore):
        self.clock = clock
        self.artifacts = artifacts

    def _as_of(self) -> date:
        return self.clock.now().astimezone(LAGOS).date() - timedelta(days=1)

    def _series(self) -> list[dict]:
        as_of = self._as_of()
        rows = []
        for sku, brand, model in SKUS:
            for region in REGIONS:
                base = _base(sku, region)
                for i in range(HISTORY_DAYS):
                    d = as_of - timedelta(days=i)
                    units = base * _noise(sku, region, d)
                    if sku == "X6851" and region == "Lagos" and i < 14:
                        units *= 1 - 0.035 * (14 - i)  # 近 14 天逐日下滑
                    rows.append({"date": d.isoformat(), "sku": sku, "brand": brand, "model": model,
                                 "region": region, "country": "NG", "units": int(round(units))})
        return rows

    def _scoped(self, scope: dict, args: dict) -> list[dict]:
        out = []
        for r in self._series():
            if not (allows(scope, "country", "NG") and allows(scope, "region", r["region"])
                    and allows(scope, "brand", r["brand"])):
                continue
            if args.get("region") and r["region"] not in args["region"]:
                continue
            if args.get("brand") and r["brand"] not in args["brand"]:
                continue
            if args.get("sku") and r["sku"] not in args["sku"]:
                continue
            out.append(r)
        return out

    def query_sell_through(self, args: dict, ctx: ToolContext) -> dict:
        days = min(args.get("days", 14), HISTORY_DAYS)
        group_by = args.get("group_by") or ["brand", "region"]
        as_of = self._as_of()
        cut_last = as_of - timedelta(days=7)
        cut_start = as_of - timedelta(days=days)
        agg: dict[tuple, dict] = defaultdict(lambda: {"units": 0, "last7": 0, "prev7": 0})
        for r in self._scoped(ctx.scope, args):
            d = date.fromisoformat(r["date"])
            if d <= cut_start:
                continue
            key = tuple(r[g] for g in group_by)
            a = agg[key]
            a["units"] += r["units"]
            if d > cut_last:
                a["last7"] += r["units"]
            elif d > cut_last - timedelta(days=7):
                a["prev7"] += r["units"]
        rows = []
        for key, a in sorted(agg.items()):
            row = dict(zip(group_by, key))
            row["units"] = a["units"]
            if days >= 14 and a["prev7"]:
                row["wow_pct"] = round((a["last7"] - a["prev7"]) / a["prev7"] * 100, 1)
            rows.append(row)
        return {"rows": rows[:500], "count": len(rows), "days": days, "as_of": as_of.isoformat(),
                "source": "dw.sell_through_daily（合成数据）", "scope": ctx.scope}

    def query_inventory(self, args: dict, ctx: ToolContext) -> dict:
        rows = []
        seen = set()
        for r in self._scoped(ctx.scope, args):
            k = (r["sku"], r["region"])
            if k in seen:
                continue
            seen.add(k)
            daily = _base(*k)
            stock = int(daily * (8 + int(hashlib.md5(str(k).encode()).hexdigest(), 16) % 30))
            rows.append({"sku": r["sku"], "brand": r["brand"], "region": r["region"], "stock_units": stock,
                         "days_of_cover": round(stock / daily, 1)})
        return {"rows": rows, "count": len(rows), "as_of": self._as_of().isoformat(),
                "source": "dw.inventory_snapshot（合成数据）"}

    def detect_decline(self, args: dict, ctx: ToolContext) -> dict:
        threshold = args.get("min_decline_pct", 15)
        as_of = self._as_of()
        series: dict[tuple, dict[str, int]] = defaultdict(dict)
        meta = {}
        for r in self._scoped(ctx.scope, {}):
            k = (r["sku"], r["region"])
            series[k][r["date"]] = r["units"]
            meta[k] = r
        anomalies = []
        for k, by_day in series.items():
            last = [by_day.get((as_of - timedelta(days=i)).isoformat(), 0) for i in range(7)]
            prev = [by_day.get((as_of - timedelta(days=i)).isoformat(), 0) for i in range(7, 14)]
            la, pa = sum(last) / 7, sum(prev) / 7
            if pa and (pa - la) / pa * 100 >= threshold:
                ref = self.artifacts.put(ctx.bot_id, "sell_through_series",
                                         {"sku": k[0], "region": k[1], "daily": by_day, "as_of": as_of.isoformat()})
                anomalies.append({"sku": k[0], "model": meta[k]["model"], "brand": meta[k]["brand"], "region": k[1],
                                  "prev7_avg": round(pa, 1), "last7_avg": round(la, 1),
                                  "decline_pct": round((pa - la) / pa * 100, 1), "ref": ref})
        anomalies.sort(key=lambda a: a["decline_pct"], reverse=True)
        return {"anomalies": anomalies, "count": len(anomalies), "as_of": as_of.isoformat(),
                "rule": f"近 7 日均值较前 7 日下滑 ≥ {threshold}%", "source": "dw.sell_through_daily（合成数据）"}


def register(reg: ToolRegistry, sd: SalesData) -> None:
    filters = {"region": {"type": "array"}, "brand": {"type": "array"}, "sku": {"type": "array"}}
    reg.register(Tool(
        name="sales-data.query_sell_through", action_class="read:sales", handler=sd.query_sell_through,
        description="查询终端销量（只读副本），按作用域过滤", scope_keys=("region", "brand"),
        schema={"type": "object", "additionalProperties": False, "properties": {
            "days": {"type": "integer", "minimum": 1}, "group_by": {"type": "array", "items": {
                "enum": ["brand", "region", "sku", "model", "date"]}}, **filters}},
    ))
    reg.register(Tool(
        name="sales-data.query_inventory", action_class="read:sales", handler=sd.query_inventory,
        description="查询库存与周转天数", scope_keys=("region", "brand"),
        schema={"type": "object", "additionalProperties": False, "properties": {"level": {"type": "string"}, **filters}},
    ))
    reg.register(Tool(
        name="sales-data.detect_decline", action_class="read:sales", handler=sd.detect_decline,
        description="识别动销下滑（7 日均值对比）",
        schema={"type": "object", "additionalProperties": False, "properties": {
            "window_days": {"type": "integer", "minimum": 7}, "min_decline_pct": {"type": "number", "minimum": 1}}},
    ))

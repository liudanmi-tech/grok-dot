"""KPI 改进建议：把当前进度（真实计算）、数字员工的进程与反馈、一线日报，与市场证据（演示用 mock）合成一份诊断报告。

结构：结论 → 差距拆解 → 根因（每条带证据与来源）→ 数字员工 / 人的执行复盘 → 分优先级的改进建议 → 采纳前后预测 → 需要领导拍板的事。
差距与建议的影响按真实进度缩放，保证数字前后自洽：建议全部落地后的预测 = 现状 + 各条建议增量。
"""
from __future__ import annotations

from datetime import date, timedelta

from . import conversations as conv

# ---------- 演示用市场证据（mock）：每个市场一套，数字只用于演示 ----------
FIXTURES = {
    "NG": {
        "sign": "₦",
        "drivers": [   # 差距归因：份额之和 = 1
            {"key": "price", "label": "竞品降价分流", "share": 0.38, "color": "#ff453a"},
            {"key": "stock", "label": "主力型号断货", "share": 0.27, "color": "#ff9f0a"},
            {"key": "field", "label": "门店执行不到位", "share": 0.20, "color": "#bf5af2"},
            {"key": "demand", "label": "需求拉动未启动", "share": 0.15, "color": "#64d2ff"},
        ],
        "findings": [
            {"id": "F1", "driver": "price", "severity": "高", "agent": "price-sentinel", "confidence": "高",
             "title": "Redmi A5 降价 8%，正面截流 Spark 40 价格带",
             "summary": "10-24 起 Redmi A5（4+128）在 Computer Village 与 Jumia 同步降价至 ₦118,000，与 Spark 40 价差从 ₦4,000 拉大到 ₦14,000；同期 Spark 40 日销下滑最明显。",
             "metrics": [{"label": "Spark 40 周环比", "value": "-26.7%", "tone": "bad"},
                         {"label": "价差", "value": "₦4k → ₦14k", "tone": "bad"},
                         {"label": "Spark 40 份额", "value": "41% → 33%", "tone": "bad"}],
             "chart": {"type": "line", "title": "Spark 40 vs Redmi A5 · 门店日销（台）", "labels": ["10-18", "10-19", "10-20", "10-21", "10-22", "10-23", "10-24", "10-25", "10-26"],
                       "series": [{"name": "Spark 40", "color": "#0a84ff", "data": [142, 138, 145, 140, 136, 131, 112, 104, 101]},
                                  {"name": "Redmi A5", "color": "#ff453a", "data": [58, 61, 57, 60, 63, 66, 92, 101, 108]}],
                       "mark": {"at": 6, "label": "竞品降价"}},
             "sources": ["竞品价格监测（Jumia / 门店巡价）", "sales-data 终端日销", "价格动销哨兵 10-26 交付卡片"]},
            {"id": "F2", "driver": "stock", "severity": "高", "agent": "biz-review", "confidence": "高",
             "title": "Ikeja、Lekki 两大商圈 Camon 40 断码 3 天",
             "summary": "Camon 40 8+256 在 Ikeja（Computer Village）与 Lekki 共 11 家门店库存为 0，经销商仓 Apapa 仍有 1,240 台未分货；断货门店贡献了区域 46% 的历史销量。",
             "metrics": [{"label": "断货门店", "value": "11 / 64 家", "tone": "bad"},
                         {"label": "经销商仓滞留", "value": "1,240 台", "tone": "warn"},
                         {"label": "断货天数", "value": "3 天", "tone": "bad"}],
             "chart": {"type": "bar", "title": "各商圈 Camon 40 门店库存天数", "unit": "天",
                       "data": [{"label": "Ikeja", "value": 0}, {"label": "Lekki", "value": 0.4}, {"label": "Surulere", "value": 6.5},
                                {"label": "Yaba", "value": 9.1}, {"label": "Ikorodu", "value": 12.3}], "threshold": 5},
             "sources": ["经销商库存日报（DMS）", "sales-data 门店维度", "经营复盘员 10-26 晚会复盘"]},
            {"id": "F3", "driver": "field", "severity": "中", "agent": "field-reporter", "confidence": "中",
             "title": "促销员报数断档，弱店无人跟进",
             "summary": "近 3 天一线报数及时率 78%，Surulere / Ikorodu 有 4 名促销员连续 2 天未报；未报门店的日销比已报门店低 31%，主管看不到问题、也就没有跟进。",
             "metrics": [{"label": "报数及时率", "value": "78%", "tone": "warn"},
                         {"label": "连续未报", "value": "4 人", "tone": "bad"},
                         {"label": "未报门店日销", "value": "-31%", "tone": "bad"}],
             "chart": {"type": "pie", "title": "近 3 天门店报数情况", "unit": "家",
                       "data": [{"label": "按时报", "value": 50, "color": "#30d158"}, {"label": "晚报", "value": 6, "color": "#ff9f0a"},
                                {"label": "未报", "value": 8, "color": "#ff453a"}]},
             "sources": ["一线日报（WhatsApp 报数）", "促销员花名册"]},
            {"id": "F4", "driver": "demand", "severity": "中", "agent": "market-intel", "confidence": "中",
             "title": "需求端没有动作：发薪日档期未做内容与活动",
             "summary": "10-25 是本地发薪周，往年同期客流 +18%，但本期没有上线任何门店活动或社媒内容；TikTok 上竞品同期投放 3 条促销短视频，累计播放 64 万。",
             "metrics": [{"label": "本期上线活动", "value": "0 场", "tone": "bad"},
                         {"label": "竞品短视频播放", "value": "64 万", "tone": "warn"},
                         {"label": "发薪周客流", "value": "+18%", "tone": "good"}],
             "chart": {"type": "bar", "title": "近 7 天社媒声量（千次互动）", "unit": "k",
                       "data": [{"label": "Redmi", "value": 41}, {"label": "itel", "value": 22}, {"label": "Samsung", "value": 18}, {"label": "TECNO", "value": 9}]},
             "sources": ["社媒监测（TikTok / Instagram）", "市场情报员 10-25 情报简报"]},
        ],
        "agent_notes": {
            "chief-of-staff": ("统筹到位，晨会派活 3/3 次准时", "跨岗位结论没有沉淀为次日待办，复盘→行动链条断开"),
            "biz-review": ("复盘结论准确，第一时间定位了断货", "断货结论只停在报告里，未触发分货动作"),
            "market-intel": ("竞品降价 24 小时内发现并预警", "线下门店价格只覆盖 Computer Village 一个商圈"),
            "price-sentinel": ("调价方案数据扎实、给了三档选项", "方案已提交 41 小时仍在等审批——瓶颈在人"),
            "dealer-assistant": ("经销商问答响应快", "未主动推送补货提醒"),
            "content-factory": ("素材质量稳定", "未被纳入本 KPI 小队，发薪周内容空档"),
            "channel-incentive": ("激励方案框架已就绪", "尚未与断货/弱店名单联动"),
        },
        "humans": [
            {"label": "晨会准时主持", "value": "3 / 3", "tone": "good"},
            {"label": "晚会准时主持", "value": "2 / 3", "tone": "warn", "note": "10-26 晚会延后到 21:40"},
            {"label": "审批平均等待", "value": "26 小时", "tone": "bad", "note": "目标 ≤ 4 小时"},
            {"label": "交付验收率", "value": "38%", "tone": "warn", "note": "大量交付未被点评，数字员工拿不到反馈"},
        ],
        "suggestions": [
            {"id": "S1", "priority": "P0", "finding": "F1", "weight": 0.38, "agent": "price-sentinel",
             "title": "Spark 40 限时 ₦6,000 补贴 + 以旧换新，收窄与 Redmi A5 的价差",
             "steps": ["今天批复价格动销哨兵的 B 档方案（补贴 ₦6,000，限 Ikeja / Lekki / Surulere）", "门店价签与 WhatsApp 话术 24 小时内同步",
                       "每天 12:00 / 18:00 两次回看价差与日销，3 天无效即止损"],
             "owner": "Lagos 销售主管", "due": "今天 21:00 前审批", "cost": 4_200_000, "effort": "低", "confidence": "高",
             "metric": "Spark 40 日销回到 ≥ 135 台", "risk": "毛利率下降约 2.1 个百分点；需财务确认补贴口径"},
            {"id": "S2", "priority": "P0", "finding": "F2", "weight": 0.27, "agent": "biz-review",
             "title": "Apapa 仓 Camon 40 紧急分货到 11 家断货门店",
             "steps": ["按门店历史日销拆分 1,240 台，优先 Ikeja / Lekki", "经销商助理通知经销商 48 小时内到店",
                       "设置库存天数 < 5 天自动预警，避免再次断货"],
             "owner": "渠道经理 · Lagos", "due": "10-29", "cost": 350_000, "effort": "中", "confidence": "高",
             "metric": "断货门店清零；Camon 40 日销 +40 台", "risk": "物流 48 小时窗口，周末可能延迟"},
            {"id": "S3", "priority": "P1", "finding": "F3", "weight": 0.20, "agent": "field-reporter",
             "title": "一线报数打卡奖励 + 未报自动提醒，弱店主管当天回访",
             "steps": ["上线「当天 20:00 前报数 +₦500」打卡奖励", "一线日报员 19:00 自动提醒未报促销员",
                       "主管每天回访 3 家未报 / 弱店，回访结论进晚会"],
             "owner": "Lagos 销售主管", "due": "10-28", "cost": 180_000, "effort": "低", "confidence": "中",
             "metric": "报数及时率 ≥ 95%；弱店日销 +15%", "risk": "奖励需防虚报：与系统出货对账"},
            {"id": "S4", "priority": "P1", "finding": "F4", "weight": 0.15, "agent": "content-factory",
             "title": "发薪周「Spark 40 学生季」内容包 + 3 场门店快闪",
             "steps": ["本地化内容工厂产出 3 条皮钦语短视频 + 门店海报", "活动安排在 Ikeja / Yaba / Surulere 周六下午",
                       "与 S1 补贴联动，统一话术「省 ₦6,000」"],
             "owner": "品牌经理 · TECNO", "due": "11-01", "cost": 900_000, "effort": "中", "confidence": "中",
             "metric": "活动门店当日销量 +30%", "risk": "内容需品牌规范审核，预留 1 天"},
            {"id": "S5", "priority": "P2", "finding": None, "weight": 0.10, "agent": "chief-of-staff",
             "title": "管理机制：审批 4 小时 SLA + 每条复盘结论自动转成次日待办",
             "steps": ["超过 4 小时未审批自动升级到国家经理", "晚会复盘结论由总调度自动生成次日晨会待办", "主管每天点评至少 3 条交付"],
             "owner": "Country Manager (NG)", "due": "本周内", "cost": 0, "effort": "低", "confidence": "中",
             "metric": "审批等待 ≤ 4 小时；交付点评率 ≥ 80%", "risk": "需要管理层配合改变习惯"},
        ],
        "asks": [
            {"title": "批准 Spark 40 限时补贴", "detail": "预算 ₦4,200,000（3 个商圈 · 7 天），对应建议 S1", "cost": 4_200_000},
            {"title": "授权跨经销商紧急分货", "detail": "允许 Apapa 仓直接分货到非签约门店，对应建议 S2", "cost": 350_000},
            {"title": "审批 4 小时 SLA 列入管理制度", "detail": "超时自动升级，对应建议 S5", "cost": 0},
        ],
    },
    "ID": {
        "sign": "Rp",
        "drivers": [
            {"key": "price", "label": "电商大促价格战", "share": 0.34, "color": "#ff453a"},
            {"key": "live", "label": "直播转化偏低", "share": 0.28, "color": "#ff9f0a"},
            {"key": "stock", "label": "门店备货不足", "share": 0.22, "color": "#bf5af2"},
            {"key": "field", "label": "促销员覆盖不足", "share": 0.16, "color": "#64d2ff"},
        ],
        "findings": [
            {"id": "F1", "driver": "price", "severity": "高", "agent": "price-sentinel", "confidence": "高",
             "title": "Redmi 14C 在 Shopee 提前开打 12.12，券后价低于 Hot 60i",
             "summary": "Redmi 14C 叠加平台券后到手 Rp1.499.000，比 Hot 60i 低 Rp100.000；Shopee 搜索「HP 1 jutaan」前 10 位中 Infinix 只占 1 位。",
             "metrics": [{"label": "券后价差", "value": "-Rp100k", "tone": "bad"},
                         {"label": "搜索前 10 占位", "value": "1 / 10", "tone": "bad"},
                         {"label": "Hot 60i 线上日销", "value": "-22%", "tone": "bad"}],
             "chart": {"type": "line", "title": "Hot 60i vs Redmi 14C · Shopee 日销（台）", "labels": ["10-19", "10-20", "10-21", "10-22", "10-23", "10-24", "10-25", "10-26", "10-27"],
                       "series": [{"name": "Hot 60i", "color": "#0a84ff", "data": [88, 92, 90, 86, 84, 72, 68, 66, 69]},
                                  {"name": "Redmi 14C", "color": "#ff453a", "data": [70, 72, 69, 74, 78, 102, 118, 121, 126]}],
                       "mark": {"at": 5, "label": "竞品券后降价"}},
             "sources": ["电商价格监测（Shopee / Tokopedia）", "sales-data 线上日销", "价格动销哨兵交付卡片"]},
            {"id": "F2", "driver": "live", "severity": "高", "agent": "ecommerce-live", "confidence": "中",
             "title": "TikTok Shop 直播有流量没转化：转化率 0.9%，行业 2.3%",
             "summary": "近 5 场直播平均观看 1.8 万，但讲解时长 70% 花在参数，没有专属券和限时口令；竞品直播间同期转化 2.6%。",
             "metrics": [{"label": "直播转化率", "value": "0.9%", "tone": "bad"},
                         {"label": "场均观看", "value": "1.8 万", "tone": "good"},
                         {"label": "专属券", "value": "无", "tone": "bad"}],
             "chart": {"type": "bar", "title": "直播转化率对比", "unit": "%",
                       "data": [{"label": "Infinix", "value": 0.9}, {"label": "Redmi", "value": 2.6}, {"label": "realme", "value": 2.1}, {"label": "行业均值", "value": 2.3}], "threshold": 2.3},
             "sources": ["TikTok Shop 后台", "电商直播运营复盘"]},
            {"id": "F3", "driver": "stock", "severity": "中", "agent": "channel-expansion", "confidence": "中",
             "title": "ITC Roxy Mas 与 Erafone 门店 GT 30 备货只够 2 天",
             "summary": "档期前门店按日常量备货，GT 30 在 Jakarta Pusat / Barat 门店平均库存天数 2.1 天，低于档期安全线 7 天。",
             "metrics": [{"label": "平均库存天数", "value": "2.1 天", "tone": "bad"},
                         {"label": "低于安全线门店", "value": "23 / 58 家", "tone": "warn"}],
             "chart": {"type": "bar", "title": "各区域 GT 30 库存天数", "unit": "天",
                       "data": [{"label": "Pusat", "value": 1.6}, {"label": "Barat", "value": 2.4}, {"label": "Selatan", "value": 5.8}, {"label": "Timur", "value": 7.9}, {"label": "Utara", "value": 8.6}], "threshold": 7},
             "sources": ["经销商库存日报", "渠道拓展员门店巡检"]},
            {"id": "F4", "driver": "field", "severity": "中", "agent": "channel-incentive", "confidence": "中",
             "title": "促销员覆盖 61%，周末高峰门店无人讲解",
             "summary": "58 家重点门店中 22 家周末无促销员，这些门店周末销量占比比有人门店低 19 个百分点。",
             "metrics": [{"label": "促销员覆盖", "value": "61%", "tone": "warn"},
                         {"label": "周末无人门店", "value": "22 家", "tone": "bad"}],
             "chart": {"type": "pie", "title": "重点门店促销员覆盖", "unit": "家",
                       "data": [{"label": "全周覆盖", "value": 36, "color": "#30d158"}, {"label": "仅工作日", "value": 9, "color": "#ff9f0a"},
                                {"label": "无人", "value": 13, "color": "#ff453a"}]},
             "sources": ["一线日报", "促销员排班表"]},
        ],
        "agent_notes": {
            "price-sentinel": ("竞品券后价追踪及时", "建议还停留在「跟价」，缺少组合券方案"),
            "ecommerce-live": ("直播排期稳定", "转化率低但未做 A/B 测试话术"),
            "channel-expansion": ("门店巡检覆盖 80%", "库存预警未推送到经销商"),
            "channel-incentive": ("激励方案已草拟", "方案在审批中排队 2 天"),
            "content-factory": ("印尼语素材质量好", "直播切片复用率低"),
            "market-intel": ("竞品动态完整", "未覆盖 Tokopedia"),
            "campaign-planner": ("12.12 档期节奏清晰", "线下与线上活动未统一口令"),
        },
        "humans": [
            {"label": "晨会准时主持", "value": "2 / 2", "tone": "good"},
            {"label": "晚会准时主持", "value": "1 / 2", "tone": "warn"},
            {"label": "审批平均等待", "value": "31 小时", "tone": "bad", "note": "目标 ≤ 4 小时"},
            {"label": "交付验收率", "value": "29%", "tone": "warn"},
        ],
        "suggestions": [
            {"id": "S1", "priority": "P0", "finding": "F1", "weight": 0.34, "agent": "price-sentinel",
             "title": "Hot 60i「券 + 分期 0%」组合，到手价对齐 Redmi 14C", "steps": ["Shopee 店铺券 Rp75.000 + Kredivo 6 期 0%", "搜索关键词「HP 1 jutaan」加投 7 天", "每日回看价差与搜索占位"],
             "owner": "Jakarta 销售主管", "due": "今天", "cost": 380_000_000, "effort": "低", "confidence": "高",
             "metric": "搜索前 10 占位 ≥ 3；线上日销回到 90 台", "risk": "需与平台确认券叠加规则"},
            {"id": "S2", "priority": "P0", "finding": "F2", "weight": 0.28, "agent": "ecommerce-live",
             "title": "直播改版：专属口令券 + 30 分钟限时价 + 对比竞品演示", "steps": ["每场设置 2 次限时价节点", "话术重排：痛点 → 对比 → 限时券", "A/B 测试两版开场"],
             "owner": "品牌经理 · Infinix", "due": "10-30", "cost": 120_000_000, "effort": "中", "confidence": "中",
             "metric": "直播转化率 ≥ 2%", "risk": "主播需培训 1 天"},
            {"id": "S3", "priority": "P1", "finding": "F3", "weight": 0.22, "agent": "channel-expansion",
             "title": "档期前把 GT 30 门店库存补到 7 天", "steps": ["Pusat / Barat 优先补货", "经销商 48 小时到店", "库存 < 5 天自动预警"],
             "owner": "Jakarta 销售主管", "due": "11-05", "cost": 60_000_000, "effort": "中", "confidence": "高",
             "metric": "低于安全线门店清零", "risk": "需占用经销商授信"},
            {"id": "S4", "priority": "P1", "finding": "F4", "weight": 0.16, "agent": "channel-incentive",
             "title": "周末兼职促销员 + 冲量阶梯奖励", "steps": ["22 家无人门店周末补兼职", "单周 ≥ 20 台额外奖励", "一线日报员跟踪报数"],
             "owner": "Jakarta 销售主管", "due": "11-02", "cost": 95_000_000, "effort": "中", "confidence": "中",
             "metric": "覆盖率 ≥ 90%", "risk": "兼职培训质量"},
            {"id": "S5", "priority": "P2", "finding": None, "weight": 0.10, "agent": "campaign-planner",
             "title": "管理机制：审批 4 小时 SLA，线上线下统一 12.12 口令", "steps": ["超时自动升级到国家经理", "统一口令「Infinix 12.12 Hemat」"],
             "owner": "Country Manager (ID)", "due": "本周内", "cost": 0, "effort": "低", "confidence": "中",
             "metric": "审批等待 ≤ 4 小时", "risk": "—"},
        ],
        "asks": [
            {"title": "批准 12.12 线上券 + 分期贴息预算", "detail": "Rp380.000.000，对应建议 S1", "cost": 380_000_000},
            {"title": "批准周末兼职促销员", "detail": "Rp95.000.000，对应建议 S4", "cost": 95_000_000},
        ],
    },
}


def _pick_bot(p, template: str, country: str) -> dict | None:
    bots = [b for b in p.bots.list() if b.template == template and b.active]
    b = next((x for x in bots if (x.scope or {}).get("country") == [country]), None) or (bots[0] if bots else None)
    if not b:
        return None
    meta = p.bots.meta.get(b.template, {})
    return {"id": b.id, "template": b.template, "display_name": b.display_name, "color": meta.get("color", "#64748b"),
            "title": meta.get("title")}


def build(p, kpi_id: str) -> dict:
    k = p.rhythm.kpi(kpi_id)
    country = (k["market"] or {}).get("code") or "NG"
    fx = FIXTURES.get(country, FIXTURES["NG"])
    pr = k["progress"]
    elapsed, total = pr["days_elapsed"] or 0, pr["days_total"]
    days_left = max(1, total - elapsed)
    actual, target, expected = pr["actual"] or 0, pr["target_total"], pr["expected"] or 0
    cur_daily = actual / elapsed if elapsed else float(k.get("baseline_daily") or 0)
    need_daily = max(0.0, (target - actual) / days_left)
    gap = max(0, expected - actual)
    proj_now = actual + cur_daily * days_left
    uplift = max(need_daily - cur_daily, pr["target_daily"] * 0.08)   # 需要补上的日均缺口（领先时也给一点进攻空间）

    # 差距拆解：按归因份额分配到期差距
    drivers = [{**d, "units": round(gap * d["share"])} for d in fx["drivers"]]

    # 建议：日均增量按权重分配缺口；第 3 天起满效
    suggestions = []
    for s in fx["suggestions"]:
        daily = uplift * s["weight"]
        ramp_days = min(3, days_left)
        units = round(daily * (days_left - ramp_days) + daily * ramp_days / 2)
        suggestions.append({**{x: v for x, v in s.items() if x != "weight"}, "impact_daily": round(daily), "impact_units": units,
                            "agent_bot": _pick_bot(p, s["agent"], country)})
    proj_plan = proj_now + sum(s["impact_units"] for s in suggestions)

    # 累计曲线：实际（真实）+ 现状外推 + 采纳后
    start = date.fromisoformat(k["start_date"])
    cum, run = [], 0
    for s in pr["series"][:elapsed]:
        run += s["actual"] or 0
        cum.append(run)
    curve = {"labels": [(start + timedelta(days=i)).strftime("%m-%d") for i in range(total)],
             "target": [round(pr["target_daily"] * (i + 1)) for i in range(total)], "actual": cum, "now": [], "plan": []}
    full = sum(s["impact_daily"] for s in suggestions)
    a, b = float(actual), float(actual)
    for j in range(1, days_left + 1):
        a += cur_daily
        b += cur_daily + full * min(1.0, j / 3)
        curve["now"].append(round(a))
        curve["plan"].append(round(b))

    # 数字员工复盘：真实交付 / 验收 / 点评 + 诊断点评（mock）
    squad = set(k.get("squad") or [])
    agents = []
    for b in p.bots.list():
        if not b.active or (b.scope or {}).get("country", [country]) != [country]:
            continue
        note = fx["agent_notes"].get(b.template)
        if not note and b.id not in squad:
            continue
        g = conv.growth(p, b.id)
        meta = p.bots.meta.get(b.template, {})
        agents.append({"id": b.id, "display_name": b.display_name, "template": b.template, "color": meta.get("color", "#64748b"),
                       "in_squad": b.id in squad, "level": g["level"], "delivered": g["delivered"], "accepted": g["accepted"],
                       "rework": g["rework"], "ratings": g["ratings"], "strength": (note or ("", ""))[0], "gap": (note or ("", ""))[1]})
    agents.sort(key=lambda x: (not x["in_squad"], -x["delivered"]))

    # 一线日报（真实）
    field = p.field.summary(country)
    findings = [{**f, "agent_bot": _pick_bot(p, f["agent"], country)} for f in fx["findings"]]
    behind = actual < expected
    pct = round(100 * actual / target, 1) if target else 0
    headline = (f"按当前节奏到期只能完成 {round(100 * proj_now / target)}%；落后的 {gap:,} 台里，"
                f"「{drivers[0]['label']}」和「{drivers[1]['label']}」占 {round(100 * (drivers[0]['share'] + drivers[1]['share']))}%。"
                f"5 条建议全部落地，预计可达 {round(100 * proj_plan / target)}%。") if behind else \
               (f"目前领先节奏，按现状到期可完成 {round(100 * proj_now / target)}%；以下建议用于巩固优势、提前锁定奖金档位。")
    return {
        "kpi": {"id": k["kpi_id"], "title": k["title"], "owner_name": k["owner_name"],
                "market": k["market"], "start": k["start_date"], "end": k["end_date"], "week_no": k["week_no"], "rag": pr["rag"]},
        "sign": fx["sign"], "generated_at": p.clock.now().isoformat()[:16].replace("T", " "),
        "summary": {"headline": headline, "actual": actual, "target": target, "expected": expected, "gap": gap, "pct": pct,
                    "pace": pr["pace"], "days_elapsed": elapsed, "days_total": total, "days_left": days_left,
                    "cur_daily": round(cur_daily), "need_daily": round(need_daily), "target_daily": pr["target_daily"],
                    "proj_now": round(proj_now), "proj_plan": round(proj_plan),
                    "proj_now_pct": round(100 * proj_now / target) if target else 0, "proj_plan_pct": round(100 * proj_plan / target) if target else 0,
                    "health": max(20, min(95, round(100 * (pr["pace"] or 1) - 15))), "behind": behind},
        "drivers": drivers, "findings": findings, "agents": agents, "humans": fx["humans"],
        "field": {"reported": field.get("reported"), "promoters": field.get("promoters"), "units": field.get("total_units"),
                  "missing": [m["name"] for m in field.get("missing", [])]},
        "suggestions": suggestions, "curve": curve, "asks": fx["asks"],
        "note": "诊断中的市场与竞品数据为演示用模拟数据；KPI 进度、数字员工交付与一线日报为系统实时数据。",
    }

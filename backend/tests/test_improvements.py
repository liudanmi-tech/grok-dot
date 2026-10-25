"""KPI 改进建议：真实进度 + 演示证据，数字自洽。"""

from app import improvements

CM, LEAD = "u-country-manager", "u-lead-lagos"


def test_improvement_report_is_consistent(p, clock):
    k = p.rhythm.create_kpi(actor=CM, title="拉各斯 TECNO 销量 +15%", brand=["TECNO"], region=["Lagos"],
                            uplift_pct=15, weeks=4, owner=LEAD)
    p.rhythm.claim(k["kpi_id"], actor=LEAD, squad=["market-intel", "price-sentinel", "biz-review"])
    clock.advance(hours=72)
    r = improvements.build(p, k["kpi_id"])
    s = r["summary"]
    assert len(r["suggestions"]) == 5 and len(r["findings"]) == 4 and r["drivers"]
    assert sum(d["units"] for d in r["drivers"]) in range(s["gap"] - 2, s["gap"] + 3)          # 差距拆解对得上
    assert abs(s["proj_plan"] - s["proj_now"] - sum(x["impact_units"] for x in r["suggestions"])) <= 1  # 预测 = 现状 + 建议增量
    assert len(r["curve"]["target"]) == s["days_total"] and len(r["curve"]["plan"]) == s["days_left"]
    assert any(a["in_squad"] for a in r["agents"]) and r["suggestions"][0]["agent_bot"]["id"]


"""目标与节奏：领导设 KPI → 主管认领组队 → 周分解 → 晨会派活 → 群广播 → 晚会点评 → 日报上报 → 次日迭代。"""
from fastapi.testclient import TestClient

from app.main import create_app

CM, LEAD = "u-country-manager", "u-lead-lagos"
SQUAD = ["market-intel", "price-sentinel", "biz-review"]


def _kpi(p):
    k = p.rhythm.create_kpi(actor=CM, title="拉各斯 TECNO 销量 +15%", brand=["TECNO"], region=["Lagos"],
                            uplift_pct=15, weeks=4, owner=LEAD)
    return p.rhythm.claim(k["kpi_id"], actor=LEAD, squad=SQUAD)


def test_kpi_baseline_and_claim_creates_week_plan(p):
    k = _kpi(p)
    assert k["status"] == "active" and k["baseline_daily"] > 0 and k["squad"] == SQUAD
    assert k["progress"]["days_total"] == 28 and k["progress"]["actual"] == 0
    w = p.rhythm.week(k["kpi_id"], 1)
    assert w["status"] == "draft" and {i["bot_id"] for i in w["items"]} == set(SQUAD)   # 每个成员都有周目标
    assert any(o["recipient"] == f"user:{LEAD}" for o in p.outbox.list())


def test_daily_loop(p, clock):
    k = _kpi(p)
    kid = k["kpi_id"]
    clock.advance(hours=2)                                   # 08:00 拉各斯，晨会时间已到
    p.rhythm.tick()
    m = p.rhythm.meetings(kid)[0]
    assert m["kind"] == "standup" and len(m["data"]["assignments"]) == 3
    assert m["mission"]["status"] == "delivered"             # 总调度派活 → 小队并行 → 汇总
    kids = p.tasks.children(m["mission_task_id"])
    assert all(c["requester_scope"]["region"] == ["Lagos"] and c["requester_scope"]["brand"] == ["TECNO"] for c in kids)
    kinds = [c["kind"] for c in p.rhythm.channel(kid)]
    assert "standup" in kinds and kinds.count("assign") == 3 and "progress" in kinds   # 群广播

    clock.advance(hours=10, minutes=30)                      # 18:30 晚会
    p.rhythm.tick()
    r = p.rhythm.meetings(kid)[0]
    assert r["kind"] == "review" and r["status"] == "draft" and len(r["data"]["rows"]) == 3
    with_note = {"price-sentinel": {"score": "behind", "comment": "促销方案要给到具体型号和门店"}}
    try:
        p.rhythm.submit_review(kid, actor=CM, ratings=with_note)
        raise AssertionError("非负责人不能主持晚会")
    except Exception as e:
        assert "负责人" in str(e)
    done = p.rhythm.submit_review(kid, actor=LEAD, ratings=with_note, adjustments="明天聚焦 Spark X6851")
    assert done["status"] == "done" and done["data"]["report"]["done"] == 3
    assert any(o["recipient"] == f"user:{CM}" and "日报" in o["card"]["title"] for o in p.outbox.list())  # 日报上报领导
    assert any("主管点评" in mm["content"] for mm in p.memory.list(namespace="bot/price-sentinel"))   # 点评沉淀

    # 次日：晨会采纳昨晚点评；KPI 有了第一天的实际数据；促销生效后销量上升
    p.sales.effects = [{"from": "2026-11-03", "region": "Lagos", "brand": "TECNO", "pct": 0.3}]
    clock.advance(hours=14)                                  # 次日 08:30
    p.rhythm.tick()
    k = p.rhythm.kpi(kid)
    assert k["progress"]["days_elapsed"] == 1 and k["progress"]["actual"] > 0
    lifted = k["progress"]["actual"]
    p.sales.effects = []
    assert lifted > p.rhythm.kpi(kid)["progress"]["actual"] * 1.25        # 结果来自销量数据，动作见效 KPI 才动
    standups = [m for m in p.rhythm.meetings(kid) if m["kind"] == "standup"]
    assert len(standups) == 2


def test_api_and_permissions(p):
    c = TestClient(create_app(p))
    body = {"title": "拉各斯 TECNO +15%", "brand": ["TECNO"], "region": ["Lagos"], "uplift_pct": 15, "owner": LEAD}
    assert c.post("/api/kpis", json=body, headers={"X-User-Id": "u-staff-lagos-tecno"}).status_code == 403
    k = c.post("/api/kpis", json=body, headers={"X-User-Id": CM}).json()
    assert c.post(f"/api/kpis/{k['kpi_id']}/claim", json={"squad": SQUAD},
                  headers={"X-User-Id": "u-lead-north"}).status_code == 400
    assert c.post(f"/api/kpis/{k['kpi_id']}/claim", json={"squad": SQUAD + ["dealer-assistant"]},
                  headers={"X-User-Id": LEAD}).json()["squad"] == SQUAD          # 对外岗位不进小队
    assert c.post(f"/api/kpis/{k['kpi_id']}/standup", headers={"X-User-Id": LEAD}).status_code == 200
    d = c.get(f"/api/kpis/{k['kpi_id']}").json()
    assert len(d["squad_cards"]) == 3 and d["meetings"][0]["kind"] == "standup" and d["channel"]
    c.post(f"/api/kpis/{k['kpi_id']}/channel", json={"text": "大家加油"}, headers={"X-User-Id": LEAD})
    assert c.get(f"/api/kpis/{k['kpi_id']}").json()["channel"][-1]["text"] == "大家加油"
    tree = c.get("/api/kpis").json()
    assert tree[0]["progress"]["rag"] is None and tree[0]["owner_name"] == "Lagos 销售主管"

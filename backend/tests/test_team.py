"""智能体库与团队：招募、按区域作用域、暂停、岗位模板共享技能与白名单。"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.orchestrator.tasks import TaskError


def test_library_and_seed(p):
    lib = {t["id"]: t for t in p.bots.library()}
    assert len(lib) == 12 and lib["market-intel"]["hired"] == 1 and lib["content-factory"]["hired"] == 0
    assert {b.id for b in p.bots.list()} == {"chief-of-staff", "market-intel", "biz-review", "price-sentinel", "dealer-assistant"}


def test_hire_regional_instance_runs_with_scope(p):
    b = p.bots.hire("biz-review", actor="u-agent-ops", region="North")
    assert b.id == "biz-review--north" and b.scope["region"] == ["North"]
    t = p.tasks.create(bot_id=b.id, goal="北部销量", source="manual", requester="u-country-manager",
                       requester_scope={"country": ["NG"]})
    p.runner.run(t["task_id"])
    t = p.tasks.get(t["task_id"])
    assert t["status"] == "delivered", t["notes"]
    assert {r["region"] for r in t["context"]["steps"]["sales"]["result"]["rows"]} == {"North"}


def test_new_templates_work(p):
    for tpl, goal in (("content-factory", "为 Spark X6851 写一组周末促销文案"),
                      ("voice-of-customer", "上月哪个型号售后问题最多"),
                      ("promoter-coach", "怎么向顾客介绍 Infinix Hot 50 的卖点")):
        b = p.bots.hire(tpl, actor="u-agent-ops")
        t = p.tasks.create(bot_id=b.id, goal=goal, source="manual", requester="u-country-manager",
                           requester_scope={"country": ["NG"]})
        p.runner.run(t["task_id"])
        assert p.tasks.get(t["task_id"])["status"] == "delivered", (tpl, p.tasks.get(t["task_id"])["notes"])


def test_paused_bot_cannot_work(p):
    p.bots.set_state("market-intel", "paused")
    with pytest.raises(TaskError):
        p.tasks.create(bot_id="market-intel", goal="x", source="manual")
    r = p.proxy.call("market-intel", "knowledge.search", {"query": "x"}, trace_id="t")
    assert r["status"] == "denied"
    started = p.scheduler.tick()["started"]
    assert started and not any(k.startswith("market-intel:") for k in started)  # 暂停的不调度，其他照常


def test_voc_handoff_reaches_biz_review(p):
    p.bots.hire("voice-of-customer", actor="u-agent-ops")
    t = p.tasks.create(bot_id="voice-of-customer", goal="例程", source="routine", skill="weekly-voc")
    p.runner.run(t["task_id"])
    assert p.tasks.get(t["task_id"])["status"] == "delivered"
    assert p.bus.list()[0]["to_bot"] == "biz-review"


def test_team_api(p):
    c = TestClient(create_app(p))
    ops = {"X-User-Id": "u-agent-ops"}
    assert c.post("/api/bots", json={"template": "promoter-coach"}, headers={"X-User-Id": "u-staff-lagos-tecno"}).status_code == 403
    r = c.post("/api/bots", json={"template": "promoter-coach", "region": "Lagos"}, headers=ops)
    assert r.status_code == 200 and r.json()["activity"] == "idle"
    assert c.post("/api/bots", json={"template": "dealer-assistant"}, headers=ops).status_code == 400  # 只能一个
    team = c.get("/api/bots", headers=ops).json()
    assert len(team) == 6
    d = c.get("/api/bots/market-intel", headers=ops).json()
    assert d["skills"] and d["summary"]
    ov = c.get("/api/overview", headers=ops).json()
    assert ov["team_size"] == 6 and "inbox" in ov
    assert c.patch("/api/bots/promoter-coach", json={"state": "paused"}, headers=ops).json()["activity"] == "paused"
    assert any(a["type"] == "bot.hire" for a in c.get("/api/activity", headers=ops).json())


def test_activity_distinguishes_off_duty_idle_working(p, clock):
    c = TestClient(create_app(p))
    act = lambda: {b["id"]: b["activity"] for b in c.get("/api/bots").json()}  # noqa: E731
    a = act()                                             # 06:00 拉各斯：晨会前
    assert a["biz-review"] == "off_duty" and a["dealer-assistant"] == "idle"   # 对外岗位 7×24
    clock.advance(hours=3)                                # 09:00 上班
    assert act()["biz-review"] == "idle"
    t = p.tasks.create(bot_id="biz-review", goal="x", source="manual")
    p.tasks.submit_plan(t["task_id"], [{"title": "a", "type": "llm"}])
    assert act()["biz-review"] == "working"

"""印尼市场：数据按国家隔离、新岗位、印尼 KPI 按雅加达时区开晨会、一键演示数据。"""
from fastapi.testclient import TestClient

from app.main import create_app
from app.tools.registry import ToolContext


def _ctx(p, scope, bot="market-intel"):
    return ToolContext(bot_id=bot, scope=scope, kb_visibility=["internal", "external"], trace_id="t", task_id=None,
                       requester=None)


def test_data_is_isolated_by_country(p):
    ng = p.tools.get("sales-data.query_sell_through").handler({"days": 14}, _ctx(p, {"country": ["NG"]}))
    idn = p.tools.get("sales-data.query_sell_through").handler({"days": 14, "group_by": ["region", "model"]},
                                                              _ctx(p, {"country": ["ID"]}))
    assert {r["region"] for r in ng["rows"]} == {"Lagos", "North", "South-West", "South-East", "FCT"}
    assert "Jakarta" in {r["region"] for r in idn["rows"]} and "Hot 60i" in {r["model"] for r in idn["rows"]}
    hot = next(r for r in idn["rows"] if r["region"] == "Jakarta" and r["model"] == "Hot 60i")
    assert hot["wow_pct"] < -10                                                  # 演示故事：雅加达 Hot 60i 下滑
    prices = p.tools.get("competitor-watch.list_price_changes").handler({"since_hours": 200}, _ctx(p, {"country": ["ID"]}))
    assert {c["currency"] for c in prices["changes"]} == {"IDR"}
    kb = p.tools.get("knowledge.search").handler({"query": "价格带 指导价"}, _ctx(p, {"country": ["ID"]}))
    assert all(r["doc_id"].startswith("kb:id-") for r in kb["results"])
    cov = p.tools.get("retail-ops.query_coverage").handler({}, _ctx(p, {"country": ["ID"], "region": ["Jakarta"]}))
    assert {r["region"] for r in cov["rows"]} == {"Jakarta"}


def test_new_roles_and_indonesia_seed(p, clock):
    c = TestClient(create_app(p))
    lib = {t["id"] for t in c.get("/api/bot-templates").json()}
    assert {"channel-expansion", "channel-incentive", "ecommerce-live", "campaign-planner"} <= lib
    r = c.post("/api/demo/seed/indonesia", headers={"X-User-Id": "u-agent-ops"}).json()
    assert len(r["squad"]) == 7 and all("--id-jakarta" in b for b in r["squad"])
    assert c.post("/api/demo/seed/indonesia", headers={"X-User-Id": "u-agent-ops"}).json() == r   # 幂等
    k = p.rhythm.kpi(r["kpi_id"])
    assert k["status"] == "active" and k["market"]["code"] == "ID" and k["owner"] == "u-lead-jakarta"
    # T0 = 拉各斯 06:00 = 雅加达 12:00：雅加达已过 07:30，tick 应开出印尼晨会
    p.rhythm.tick()
    m = p.rhythm.meetings(r["kpi_id"])[0]
    assert m["kind"] == "standup" and m["mission"]["status"] == "delivered"
    kids = p.tasks.children(m["mission_task_id"])
    assert kids and all(x["requester_scope"]["country"] == ["ID"] for x in kids)
    assert all(x["status"] in ("delivered", "awaiting_approval") for x in kids), [(x["bot_id"], x["status"], x["notes"]) for x in kids]
    # 尼日利亚的成员不能进印尼小队
    try:
        p.rhythm.claim(r["kpi_id"], actor="u-lead-jakarta", squad=["market-intel"])
        raise AssertionError("跨市场成员不应被接受")
    except Exception as e:
        assert "印尼" in str(e)

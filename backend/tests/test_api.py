from fastapi.testclient import TestClient

from app.bots.hermes_adapter import bot_token
from app.main import create_app


def client(p):
    return TestClient(create_app(p))


def test_task_flow_over_http(p):
    c = client(p)
    H = {"X-User-Id": "u-staff-lagos-tecno"}
    assert c.get("/api/me").status_code == 401
    r = c.post("/api/tasks", json={"bot_id": "biz-review", "goal": "Lagos TECNO 上周销量"}, headers=H)
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]
    t = c.get(f"/api/tasks/{tid}", headers=H).json()
    assert t["status"] == "delivered" and t["timeline"]
    # 别人不能验收
    r = c.post(f"/api/tasks/{tid}/action", json={"action": "useful"}, headers={"X-User-Id": "u-country-manager"})
    assert r.status_code == 400
    assert c.post(f"/api/tasks/{tid}/action", json={"action": "useful"}, headers=H).json()["status"] == "accepted"


def test_approval_over_http(p):
    c = client(p)
    cm = {"X-User-Id": "u-country-manager"}
    c.post("/api/routines/price-sentinel/daily-scan/run", headers=cm)
    pending = c.get("/api/approvals?state=pending&mine=true", headers=cm).json()
    assert pending
    r = c.post(f"/api/approvals/{pending[0]['approval_id']}/decide", json={"decision": "approve"}, headers=cm)
    assert r.json()["state"] == "executed"
    assert c.get("/api/audit/verify").json()["ok"]


def test_internal_endpoints_require_bot_token(p):
    c = client(p)
    body = {"messages": [{"role": "user", "content": "hi"}]}
    assert c.post("/v1/chat/completions", json=body, headers={"X-Bot-Id": "market-intel"}).status_code == 401
    r = c.post("/v1/chat/completions", json=body,
               headers={"X-Bot-Id": "market-intel", "X-Bot-Token": bot_token("market-intel")})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"]
    r = c.post("/internal/tools/call", json={"tool": "knowledge.search", "args": {"query": "价格政策"},
                                             "trace_id": "h1"},
               headers={"X-Bot-Id": "market-intel", "X-Bot-Token": bot_token("market-intel")})
    assert r.json()["status"] == "ok"

"""OpenRouter：控制台配置 Key（只写不读）、按档位选模型、走真实费用、失败时降级。"""
import httpx
from fastapi.testclient import TestClient

from app.main import create_app

KEY = "sk-or-v1-" + "a" * 40


class FakeResp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def test_settings_masked_and_role_checked(p):
    c = TestClient(create_app(p))
    staff = {"X-User-Id": "u-staff-lagos-tecno"}
    ops = {"X-User-Id": "u-agent-ops"}
    assert c.get("/api/settings/models", headers=ops).json()["configured"] is False
    assert c.put("/api/settings/models", json={"api_key": KEY}, headers=staff).status_code == 403
    assert c.put("/api/settings/models", json={"api_key": "nope"}, headers=ops).status_code == 400
    r = c.put("/api/settings/models", json={"api_key": KEY, "models": {"standard": "openai/gpt-4o-mini"}}, headers=ops)
    body = r.json()
    assert body["configured"] and body["key_hint"].endswith("aaaa") and KEY not in r.text
    assert body["models"]["standard"] == "openai/gpt-4o-mini"
    assert KEY not in c.get("/api/settings/models", headers=ops).text
    ev = p.audit.list(event_type="settings.change")[0]
    assert KEY not in str(ev["payload"])


def test_gateway_uses_openrouter_with_real_cost(p, monkeypatch):
    p.gateway.settings.update(actor="t", at="now", api_key=KEY, models={"standard": "openai/gpt-4o-mini"})
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, model=json["model"], auth=headers["Authorization"])
        return FakeResp(200, {"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": "Hello dealer"}}],
                              "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.0123}})
    monkeypatch.setattr(httpx, "post", fake_post)
    out = p.gateway.chat("dealer-assistant", [{"role": "user", "content": "hi"}], trace_id="tr")
    assert seen["url"].startswith("https://openrouter.ai/") and seen["model"] == "openai/gpt-4o-mini"
    assert out["model"] == "openai/gpt-4o-mini" and out["fallback_from"] is None and out["cost_usd"] == 0.0123
    assert p.db.one("SELECT SUM(cost_usd) s FROM usage")["s"] == 0.0123


def test_openrouter_failure_falls_back(p, monkeypatch):
    p.gateway.settings.update(actor="t", at="now", api_key=KEY)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(401, {"error": {"message": "invalid key"}}))
    out = p.gateway.chat("dealer-assistant", [{"role": "user", "content": "hi"}], trace_id="tr")
    assert out["model"] == "mock-local" and out["fallback_from"] == "or-standard"
    fb = p.audit.list(event_type="model.fallback")[0]["payload"]
    assert any("invalid key" in r for r in fb["reasons"])

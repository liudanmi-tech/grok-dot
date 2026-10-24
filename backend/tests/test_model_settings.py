"""OpenRouter：控制台配置 Key（只写不读）、按档位选模型、走真实费用、失败时降级。"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway import providers
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

    def fake_post(url, headers, payload, timeout):
        seen.update(url=url, model=payload["model"], auth=headers["Authorization"])
        return FakeResp(200, {"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": "Hello dealer"}}],
                              "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.0123}})
    monkeypatch.setattr(providers, "post_with_deadline", fake_post)
    out = p.gateway.chat("dealer-assistant", [{"role": "user", "content": "hi"}], trace_id="tr")
    assert seen["url"].startswith("https://openrouter.ai/") and seen["model"] == "openai/gpt-4o-mini"
    assert out["model"] == "openai/gpt-4o-mini" and out["fallback_from"] is None and out["cost_usd"] == 0.0123
    assert p.db.one("SELECT SUM(cost_usd) s FROM usage")["s"] == 0.0123


def test_openrouter_failure_falls_back(p, monkeypatch):
    p.gateway.settings.update(actor="t", at="now", api_key=KEY)
    monkeypatch.setattr(providers, "post_with_deadline", lambda *a, **k: FakeResp(401, {"error": {"message": "invalid key"}}))
    out = p.gateway.chat("dealer-assistant", [{"role": "user", "content": "hi"}], trace_id="tr")
    assert out["model"] == "mock-local" and out["fallback_from"] == "or-standard"
    fb = p.audit.list(event_type="model.fallback")[0]["payload"]
    assert any("invalid key" in r for r in fb["reasons"])


def test_keepalive_whitespace_cannot_hang_forever():
    """OpenRouter 非流式请求会持续发空白保活：必须按总时长截止，否则任务永远停在「执行中」。"""
    class Slow(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            for _ in range(50):
                self.wfile.write(b" \n")
                self.wfile.flush()
                time.sleep(0.1)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        t0 = time.monotonic()
        with pytest.raises(httpx.TimeoutException):
            providers.post_with_deadline(f"http://127.0.0.1:{srv.server_port}/", headers={}, payload={}, timeout=0.5)
        assert time.monotonic() - t0 < 2
    finally:
        srv.shutdown()

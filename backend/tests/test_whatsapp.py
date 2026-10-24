"""WhatsApp MVP：经销商问答 → 预审 → 审批 → 发送；身份绑定、关键词、作用域、签名、窗口、熔断。"""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.channels.whatsapp import detect_language
from app.main import create_app

DEALER_PHONE = "2348000000001"   # D1023 Ikeja Phones（Lagos）


def test_language_detection():
    assert detect_language("Abeg how much be X6851 now? I dey find am")[0] == "pcm"
    assert detect_language("Sannu, nawa ne wayar Spark?")[0] == "ha"
    assert detect_language("How much is the Spark X6851?")[0] == "en"


def test_dealer_question_to_approved_reply(p, users):
    r = p.whatsapp.receive(DEALER_PHONE, "How much is the TECNO Spark X6851 and do I have stock?", simulated=True)
    assert r["status"] == "queued"
    cid = r["conversation_id"]
    task = p.tasks.list(bot_id="dealer-assistant")[0]
    # 作用域由网关注入：只有本经销商、本会话
    assert task["requester_scope"]["dealer_id"] == ["D1023"]
    assert task["status"] in ("delivered", "needs_human"), task["notes"]
    approvals = [a for a in p.approvals.list() if a["tool"] == "dealer-msg.submit_reply"]
    assert approvals, task["notes"]
    a = approvals[0]
    assert a["approver_role"] == "channel_manager" and a["target"].startswith("D1023")
    if a["state"] == "pending":
        rec = p.approvals.decide(a["approval_id"], actor=users["u-channel-lagos"], decision="approve")
        assert rec["state"] == "executed", rec.get("result")
        outs = [m for m in p.whatsapp.messages(cid) if m["direction"] == "out" and m["kind"] == "reply"]
        assert outs and outs[0]["status"] == "simulated"


def test_reply_cannot_reach_other_dealer(p):
    p.whatsapp.receive(DEALER_PHONE, "hi", simulated=True)
    task = p.tasks.list(bot_id="dealer-assistant")[0]
    # 模型即使想换会话也做不到：作用域来自台账，工具没有会话参数
    r = p.proxy.call("dealer-assistant", "dealer-msg.get_dealer_profile", {"dealer_id": "D2051"},
                     trace_id=task["trace_id"], requester_scope=task["requester_scope"])
    assert r["status"] == "error" and r["code"] == "INVALID_ARGS"
    r = p.proxy.call("dealer-assistant", "dealer-msg.get_dealer_profile", {}, trace_id=task["trace_id"],
                     requester_scope=task["requester_scope"])
    assert r["result"]["dealer_id"] == "D1023"
    # 只能检索对外文档
    kb = p.proxy.call("dealer-assistant", "knowledge.search", {"query": "价格政策 price"}, trace_id="k",
                      requester_scope=task["requester_scope"])
    assert all(d["visibility"] == "external" for d in kb["result"]["results"])


def test_unknown_number_keywords_and_handoff(p):
    r = p.whatsapp.receive("2348099999999", "hello", simulated=True)
    assert r["status"] == "unknown_number"
    assert not p.tasks.list(bot_id="dealer-assistant")
    assert p.whatsapp.receive(DEALER_PHONE, "LANG HA", simulated=True)["status"] == "lang_set"
    assert p.whatsapp.receive(DEALER_PHONE, "agent", simulated=True)["status"] == "handoff"
    assert p.whatsapp.receive(DEALER_PHONE, "still there?", simulated=True)["status"] == "human_mode"
    assert any(o["card"]["title"].startswith("WhatsApp 转人工") for o in p.outbox.list())
    assert p.whatsapp.receive(DEALER_PHONE, "STOP", simulated=True)["status"] == "opted_out"


def test_window_and_killswitch(p, clock):
    r = p.whatsapp.receive(DEALER_PHONE, "agent", simulated=True)
    cid = r.get("conversation_id") or p.whatsapp.list_conversations()[0]["conversation_id"]
    p.whatsapp.set_killswitch(True, "u-agent-ops")
    with pytest.raises(RuntimeError, match="熔断"):
        p.whatsapp.send(cid, "test")
    p.whatsapp.set_killswitch(False, "u-agent-ops")
    clock.advance(hours=25)
    with pytest.raises(RuntimeError, match="窗口"):
        p.whatsapp.send(cid, "test")


def test_webhook_signature_and_verify(p, monkeypatch):
    monkeypatch.setenv("WA_APP_SECRET", "s3cret")
    monkeypatch.setenv("WA_VERIFY_TOKEN", "vt")
    c = TestClient(create_app(p))
    assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt",
                                               "hub.challenge": "42"}).text == "42"
    assert c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 403
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": DEALER_PHONE, "id": "wamid.1", "type": "text", "text": {"body": "hi"}}]}}]}]}).encode()
    assert c.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    r = c.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sig,
                                                            "Content-Type": "application/json"})
    assert r.json()["messages"] == 1
    # 重投去重
    c.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert len([m for m in p.whatsapp.messages(p.whatsapp.list_conversations()[0]["conversation_id"])
                if m["direction"] == "in"]) == 1


def test_tunnel_only_exposes_webhook(p, monkeypatch):
    monkeypatch.setenv("WA_VERIFY_TOKEN", "vt")
    c = TestClient(create_app(p))
    pub = {"Host": "abc.ngrok-free.app", "X-User-Id": "u-country-manager"}
    assert c.get("/api/health", headers=pub).status_code == 404
    assert c.get("/api/tasks", headers=pub).status_code == 404
    r = c.get("/webhooks/whatsapp", headers=pub,
              params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "7"})
    assert r.text == "7"

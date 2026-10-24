"""技能广场（开源方法技能 + 安装到岗位 + 执行时按目标注入）与知识库（上传、审定后才对数字员工可见、按国家隔离）。"""
import json

from fastapi.testclient import TestClient

from app.main import create_app

OPS = {"X-User-Id": "u-agent-ops"}


def test_skill_plaza_catalog_install_and_injection(p, monkeypatch):
    c = TestClient(create_app(p))
    d = c.get("/api/skill-plaza").json()
    ids = {x["id"] for x in d["packs"]}
    assert "marketingskills/copywriting" in ids and "anthropic/internal-comms" in ids and len(ids) >= 50
    copy = next(x for x in d["packs"] if x["id"] == "marketingskills/copywriting")
    assert copy["zh"] == "转化文案写作" and "content-factory" in copy["installed"] and copy["source"]["license"] == "MIT"
    assert d["workflows"]                                                     # 内置流程技能也在广场里
    full = c.get("/api/skill-plaza/pack", params={"id": "marketingskills/pricing"}).json()
    assert len(full["body"]) > 500
    assert c.post("/api/skill-plaza/install", json={"id": "marketingskills/pricing", "templates": ["biz-review"]},
                  headers={"X-User-Id": "u-staff-lagos-tecno"}).status_code == 403
    r = c.post("/api/skill-plaza/install", json={"id": "marketingskills/pricing", "templates": ["biz-review", "nope"]}, headers=OPS)
    assert r.json()["installed"] == ["biz-review"]

    seen = {}
    real = p.gateway.chat

    def spy(bot_id, messages, **kw):
        if "【交付格式】" in messages[0]["content"]:
            seen["system"] = messages[0]["content"]
        return real(bot_id, messages, **kw)
    monkeypatch.setattr(p.gateway, "chat", spy)
    p.bots.hire("content-factory", actor="u-agent-ops")
    t = p.tasks.create(bot_id="content-factory", goal="为 Spark X6851 写一组促销文案 copy", source="manual",
                       requester="u-country-manager", requester_scope={"country": ["NG"]})
    p.runner.run(t["task_id"])
    assert "【方法技能 · 来自技能广场】" in seen["system"] and "转化文案写作" in seen["system"]
    assert p.audit.list(event_type="skill_pack.used")


def test_kb_upload_approve_and_isolation(p):
    c = TestClient(create_app(p))
    body = {"title": "雅加达门店陈列新规", "text": "Hot 60i 须放在柜台 C 位，展示机常亮，价格牌与指导价一致。", "doc_type": "channel",
            "country": "ID", "visibility": "internal"}
    assert c.post("/api/kb", json=body, headers={"X-User-Id": "u-staff-lagos-tecno"}).status_code == 403
    doc = c.post("/api/kb", json=body, headers={"X-User-Id": "u-lead-jakarta"}).json()
    assert doc["approved"] is False and doc["version"] == 1
    hits = lambda country: [r["doc_id"] for r in c.get("/api/kb/search", params={"q": "陈列 C位 展示机", "country": country}).json()["results"]]  # noqa: E731
    assert doc["doc_id"] not in hits("ID")                                  # 未审定：数字员工看不到
    assert c.post("/api/kb/doc/approve", params={"id": doc["doc_id"]}, headers={"X-User-Id": "u-lead-jakarta"}).status_code == 403
    c.post("/api/kb/doc/approve", params={"id": doc["doc_id"]}, headers=OPS)
    assert doc["doc_id"] in hits("ID") and doc["doc_id"] not in hits("NG")   # 审定后可见，且按国家隔离
    upd = c.put("/api/kb/doc", params={"id": doc["doc_id"]}, json={"text": "改：Hot 60i 与 GT 30 并列 C 位。"}, headers=OPS).json()
    assert upd["version"] == 2 and upd["approved"] is False                 # 改了就要重新审定
    assert c.put("/api/kb/doc", params={"id": "kb:price-band-2026q4"}, json={"text": "x"}, headers=OPS).status_code == 404
    c.delete("/api/kb/doc", params={"id": doc["doc_id"]}, headers=OPS)
    assert doc["doc_id"] not in {d["doc_id"] for d in c.get("/api/kb").json()}
    assert any(d["source"] == "builtin" for d in c.get("/api/kb").json())

"""任务对话视图：顶层任务即群聊；消息流、交付、待办、成员；数字员工成长；驾驶舱今日进展。"""
import json

from fastapi.testclient import TestClient

from app.main import create_app

CM = {"X-User-Id": "u-country-manager"}
CARD = {"title": "动销结论", "headline": "X6851 下滑最多", "sections": [{"heading": "a", "bullets": ["b"]}],
        "actions": [{"owner": "价格哨兵", "action": "提交促销方案", "due": "周三"}]}


def test_conversation_flow(p, monkeypatch):
    real = p.gateway.chat

    def fake(bot_id, messages, **kw):
        if "【交付格式】" in messages[0]["content"]:
            return {"content": json.dumps(CARD, ensure_ascii=False), "model": "f", "cost_usd": 0, "input_tokens": 1,
                    "output_tokens": 1, "fallback_from": None}
        return real(bot_id, messages, **kw)
    monkeypatch.setattr(p.gateway, "chat", fake)
    c = TestClient(create_app(p))
    t = c.post("/api/tasks", json={"bot_id": "chief-of-staff", "goal": "竞品调价和动销怎么样",
                                   "members": ["market-intel"]}, headers=CM).json()
    d = c.get(f"/api/conversations/{t['task_id']}").json()
    assert [k["bot_id"] for k in d["children"]] == ["market-intel"]                 # 只在选定群成员里分工
    kinds = [m["kind"] for m in d["messages"]]
    assert kinds[0] == "user" and "deliverable" in kinds and d["members"] == ["chief-of-staff", "market-intel"]
    plan = next(m for m in d["messages"] if m.get("assign"))
    assert plan["assign"][0]["bot_id"] == "market-intel"
    assert any(x["kind"] == "action" and x["text"] == "提交促销方案" for x in d["todos"])
    assert len(d["deliverables"]) == 2 and d["deliverables"][-1]["summary"]
    c.post(f"/api/tasks/{t['task_id']}/followup", json={"text": "再看看销量", "bot_id": "biz-review"}, headers=CM)
    d = c.get(f"/api/conversations/{t['task_id']}").json()
    assert any(m["kind"] == "user" and m.get("mention") == "biz-review" for m in d["messages"])
    lst = c.get("/api/conversations").json()
    assert [x["task_id"] for x in lst] == [t["task_id"]]                              # 子任务不单独成对话
    assert c.get("/api/cockpit/today").json()["conversations"][0]["task_id"] == t["task_id"]
    emp = {e["id"]: e for e in c.get("/api/employees").json()}
    assert emp["market-intel"]["growth"]["delivered"] == 1 and emp["market-intel"]["growth"]["level"].startswith("L1")

"""总调度：一句话 → 分工并行 → 免打扰 → 汇总交付；群聊式追加（@成员 / 交给总调度）。"""
import json

from fastapi.testclient import TestClient

from app.main import create_app

CM = "u-country-manager"
SCOPE = {"country": ["NG"], "region": ["Lagos"]}


def _mission(p, goal):
    t = p.tasks.create(bot_id="chief-of-staff", goal=goal, source="manual", requester=CM, requester_scope=SCOPE)
    p.runner.run(t["task_id"])
    return p.tasks.get(t["task_id"])


def test_fallback_dispatch_runs_members_in_parallel_and_synthesizes(p):
    t = _mission(p, "下周拉各斯 TECNO 要做促销：先看竞品价格和各型号动销")
    assert t["status"] == "delivered", t["notes"]
    kids = p.tasks.children(t["task_id"])
    assert {k["bot_id"] for k in kids} == {"market-intel", "price-sentinel"}
    assert all(k["status"] == "delivered" and k["source"] == "coordinator" for k in kids)
    assert all(k["requester_scope"] == SCOPE for k in kids)          # 子任务继承下达人权限快照（OBO）
    assert [s["type"] for s in t["steps"]] == ["llm", "subtask", "subtask", "llm"]
    assert all(s["status"] == "done" for s in t["steps"]) and t["outputs"][0]["step"] == "synthesize"
    # 免打扰：子任务进度卡片只挂在父任务下
    recips = {r["recipient"] for r in p.outbox.list()}
    assert f"user:{CM}" in recips and f"task:{t['task_id']}" in recips


def test_model_plan_is_validated(p, monkeypatch):
    real = p.gateway.chat

    def fake(bot_id, messages, **kw):
        if kw.get("task_type") == "plan":
            plan = {"brief": "复盘 + 竞品", "assignments": [
                {"bot_id": "biz-review", "goal": "拉各斯 TECNO 近两周销量变化", "why": "数据"},
                {"bot_id": "dealer-assistant", "goal": "不该派给对外岗位", "why": "x"},
                {"bot_id": "nobody", "goal": "不存在", "why": "x"}]}
            return {"content": "好的：\n" + json.dumps(plan, ensure_ascii=False), "model": "fake", "cost_usd": 0.001,
                    "input_tokens": 1, "output_tokens": 1, "fallback_from": None}
        return real(bot_id, messages, **kw)
    monkeypatch.setattr(p.gateway, "chat", fake)
    t = _mission(p, "拉各斯 TECNO 最近怎么样")
    assert [k["bot_id"] for k in p.tasks.children(t["task_id"])] == ["biz-review"]
    assert t["context"]["brief"] == "复盘 + 竞品" and t["status"] == "delivered"


def test_followup_mention_and_api(p):
    c = TestClient(create_app(p))
    h = {"X-User-Id": CM}
    t = _mission(p, "看看竞品最近的调价")
    r = c.post(f"/api/tasks/{t['task_id']}/followup", json={"text": "再拉一下拉各斯各型号销量", "bot_id": "biz-review"},
               headers=h)
    assert r.status_code == 200, r.text
    t = p.tasks.get(t["task_id"])
    assert t["status"] == "delivered" and len(t["context"]["assignments"]) == 3
    assert t["context"]["followups"][0]["to"] == "biz-review"
    # 只有下达人可以追加
    assert c.post(f"/api/tasks/{t['task_id']}/followup", json={"text": "x"},
                  headers={"X-User-Id": "u-agent-ops"}).status_code == 400
    d = c.get("/api/bots/chief-of-staff", headers=h).json()
    assert len(d["tasks"][0]["children"]) == 3
    kid = c.get("/api/bots/biz-review", headers=h).json()["tasks"][0]
    assert kid["parent"]["bot_id"] == "chief-of-staff"
    ov = c.get("/api/overview", headers=h).json()
    assert [x["task_id"] for x in ov["inbox"]["review"]] == [t["task_id"]]   # 子任务不进验收收件箱


def test_rework_replans_and_resynthesizes(p):
    t = _mission(p, "看看竞品最近的调价")
    p.tasks.feedback(t["task_id"], actor_id=CM, verdict="rework", note="补充售后投诉情况")
    t = p.tasks.get(t["task_id"])
    assert t["status"] == "delivered", t["notes"]

"""任务台账、技能执行、例程调度与补偿、模型网关降级与预算。"""
from datetime import timedelta

import pytest

from app.orchestrator.tasks import TaskError


def test_daily_brief_end_to_end(p):
    task = p.scheduler.trigger("market-intel", "daily-brief")
    t = p.tasks.get(task["task_id"])
    assert t["status"] == "delivered", t["notes"]
    assert t["progress_done"] == t["progress_total"] == 6
    assert t["outputs"] and "模拟输出" in t["outputs"][0]["content"]  # 未配置密钥 → 降级到 mock
    events = {e["event_type"] for e in p.audit.list(trace_id=t["trace_id"], limit=500)}
    assert {"task.create", "task.plan", "tool.call", "model.call", "model.fallback", "bus.send"} <= events
    # 早报发到岗位登记的群；大幅调价转交给哨兵
    assert any(o["recipient"] == "group:ng-market-intel" and o["kind"] == "message" for o in p.outbox.list())
    assert p.bus.list()[0]["to_bot"] == "price-sentinel"
    assert p.audit.verify()["ok"]


def test_manual_task_scope_and_delivery_to_requester(p, users):
    staff = users["u-staff-lagos-tecno"]
    t = p.tasks.create(bot_id="biz-review", goal="列出各区域毛利和销量", requester=staff["id"],
                       requester_scope=staff["scope"])
    p.runner.run(t["task_id"])
    t = p.tasks.get(t["task_id"])
    assert t["status"] == "delivered"
    rows = t["context"]["steps"]["sales"]["result"]["rows"]
    assert {r["region"] for r in rows} == {"Lagos"} and {r["brand"] for r in rows} == {"TECNO"}
    card = [o for o in p.outbox.list() if o["card_id"] == t["card_id"]][0]
    assert card["recipient"] == f"user:{staff['id']}" and card["card"]["buttons"] == ["有用", "需修改"]


def test_large_task_needs_confirmation(p, users):
    cm = users["u-country-manager"]
    t = p.tasks.create(bot_id="price-sentinel", goal="扫描动销", requester=cm["id"], requester_scope=cm["scope"],
                       skill="daily-scan")
    p.runner.run(t["task_id"])
    t = p.tasks.get(t["task_id"])
    assert t["status"] == "awaiting_confirmation"  # 含二级动作 → 大任务
    with pytest.raises(TaskError):
        p.tasks.confirm(t["task_id"], actor_id="u-staff-lagos-tecno", decision="approve")
    p.tasks.confirm(t["task_id"], actor_id=cm["id"], decision="approve")
    t = p.tasks.get(t["task_id"])
    assert t["status"] == "delivered", t["notes"]
    assert len(t["approvals"]) >= 1
    assert p.approvals.get(t["approvals"][0])["state"] == "pending"


def test_step_done_requires_tool_call_evidence(p):
    t = p.tasks.create(bot_id="market-intel", goal="x", source="routine", skill="daily-brief")
    p.tasks.submit_plan(t["task_id"], [{"title": "拉价格", "type": "tool",
                                        "tool": "competitor-watch.list_price_changes"}])
    with pytest.raises(TaskError, match="工具调用记录"):
        p.tasks.step(t["task_id"], 0, "done")
    p.proxy.call("market-intel", "competitor-watch.list_price_changes", {}, trace_id=t["trace_id"])
    assert p.tasks.step(t["task_id"], 0, "done")["progress_done"] == 1


def test_replan_limit_and_stall(p, clock):
    t = p.tasks.create(bot_id="market-intel", goal="x", source="routine", skill="daily-brief")
    step = [{"title": "a", "type": "llm"}]
    p.tasks.submit_plan(t["task_id"], step)
    p.tasks.submit_plan(t["task_id"], step, reason="r1")
    p.tasks.submit_plan(t["task_id"], step, reason="r2")
    assert p.tasks.submit_plan(t["task_id"], step, reason="r3")["status"] == "needs_human"

    t2 = p.tasks.create(bot_id="market-intel", goal="y", source="routine", skill="daily-brief")
    p.tasks.submit_plan(t2["task_id"], step)
    retried = []
    p.tasks.on_retry = retried.append
    clock.advance(minutes=11)
    p.tasks.tick()
    assert any("可能卡住" in n["text"] for n in p.tasks.get(t2["task_id"])["notes"])
    clock.advance(minutes=10)
    p.tasks.tick()
    assert retried == [t2["task_id"]]
    clock.advance(minutes=21)
    p.tasks.tick()
    assert p.tasks.get(t2["task_id"])["status"] == "needs_human"


def test_budget_pause(p):
    t = p.tasks.create(bot_id="market-intel", goal="x", source="routine", skill="daily-brief")
    p.tasks.add_cost(t["task_id"], 1.7)
    assert any("80%" in n["text"] for n in p.tasks.get(t["task_id"])["notes"])
    assert p.tasks.add_cost(t["task_id"], 0.4)["status"] == "paused"


def test_scheduler_idempotent_and_compensation(p, clock):
    # 06:00 拉各斯：05:00 情报员早报、05:30 哨兵扫描都已到点 → 补跑；再次 tick 不重复
    first = p.scheduler.tick()
    assert len(first["started"]) == 2
    assert p.scheduler.tick()["started"] == []
    # 13:00 才恢复的场景：已过 12:00 补跑时限 → 标记跳过并告警
    p2_clock = clock
    p2_clock.advance(days=1, hours=7)
    out = p.scheduler.tick()
    assert out["skipped"] and not any("market-intel" in k for k in out["started"])


def test_gateway_fallback_and_circuit_breaker(p):
    p.gateway.mock.fail_models.add("mock-local")
    with pytest.raises(Exception):
        p.gateway.chat("market-intel", [{"role": "user", "content": "hi"}])
    p.gateway.mock.fail_models.clear()
    out = p.gateway.chat("market-intel", [{"role": "user", "content": "hi"}], trace_id="tg")
    assert out["model"] == "mock-local" and out["fallback_from"] == "qwen-plus"


def test_gateway_blocks_secrets_and_respects_data_class(p):
    with pytest.raises(Exception, match="凭证"):
        p.gateway.chat("market-intel", [{"role": "user", "content": "token: sk-abcdefghijklmnopqrstuv"}])
    models = p.gateway.plan("market-intel", None, None, "personal")
    assert models == ["mock-local"]


def test_global_budget(p):
    p.db.execute("INSERT INTO usage(ts, bot_id, model, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?,?)",
                 [(p.clock.now() - timedelta(minutes=1)).isoformat(), "content-factory", "x", 0, 0, 1000])
    from app.gateway.service import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        p.gateway.plan("market-intel", None, None, "internal")
    assert p.gateway.plan("dealer-assistant", None, None, "internal")  # 对外岗位仍放行

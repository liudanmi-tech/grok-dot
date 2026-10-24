"""工具代理、代办权限、审批哈希绑定、自动预审、升级过期、Bot 间白名单。"""
import pytest

from app.approvals import ApprovalError


def test_tool_whitelist_and_scope_narrowing(p, users):
    # 情报员没有 sales-data 权限
    r = p.proxy.call("market-intel", "sales-data.query_sell_through", {}, trace_id="t1")
    assert r["status"] == "denied" and r["code"] == "SCOPE_DENIED"
    # 代办：普通员工（Lagos · TECNO）通过复盘员只能看到自己的范围
    staff = users["u-staff-lagos-tecno"]
    r = p.proxy.call("biz-review", "sales-data.query_sell_through", {"group_by": ["brand", "region"]},
                     trace_id="t2", requester=staff["id"], requester_scope=staff["scope"])
    assert r["status"] == "ok"
    assert {(row["brand"], row["region"]) for row in r["result"]["rows"]} == {("TECNO", "Lagos")}
    # 显式要求范围外的区域 → 拒绝
    r = p.proxy.call("biz-review", "sales-data.query_sell_through", {"region": ["North"]}, trace_id="t3",
                     requester=staff["id"], requester_scope=staff["scope"])
    assert r["code"] == "SCOPE_DENIED"
    # 例程（无下达人）按 Bot 权限，看全国
    r = p.proxy.call("biz-review", "sales-data.query_sell_through", {"group_by": ["region"]}, trace_id="t4")
    assert len(r["result"]["rows"]) == 5
    assert any(e["event_type"] == "policy.deny" for e in p.audit.list(trace_id="t3"))


def _submit_pricing(p):
    det = p.proxy.call("price-sentinel", "sales-data.detect_decline", {"min_decline_pct": 15}, trace_id="tp")
    a = det["result"]["anomalies"][0]
    assert a["sku"] == "X6851" and a["region"] == "Lagos"
    d = p.proxy.call("price-sentinel", "pricing-draft.create_suggestion", {
        "sku": a["sku"], "region": a["region"], "proposal_type": "promotion",
        "rationale": f"7 日均值下滑 {a['decline_pct']}%", "evidence_refs": [a["ref"]],
    }, trace_id="tp")
    return p.proxy.call("price-sentinel", "pricing-draft.submit_suggestion",
                        {"draft_id": d["result"]["draft_id"]}, trace_id="tp")


def test_pricing_approval_flow_and_hash_binding(p, users):
    r = _submit_pricing(p)
    assert r["status"] == "pending_approval"
    aid = r["approval_id"]
    # 非审批角色不能批
    with pytest.raises(ApprovalError):
        p.approvals.decide(aid, actor=users["u-channel-lagos"], decision="approve")
    rec = p.approvals.decide(aid, actor=users["u-country-manager"], decision="approve")
    assert rec["state"] == "executed"
    assert p.pricing.list()[0]["state"] == "approved"

    # 篡改存储内容 → 哈希不一致，审批失效
    r2 = _submit_pricing(p)
    p.db.execute("UPDATE approvals SET payload=REPLACE(payload, 'drf_', 'drX_') WHERE approval_id=?",
                 [r2["approval_id"]])
    with pytest.raises(ApprovalError, match="哈希"):
        p.approvals.decide(r2["approval_id"], actor=users["u-country-manager"], decision="approve")
    assert p.approvals.get(r2["approval_id"])["state"] == "failed"


def test_reject_requires_reason_and_escalation_expiry(p, users, clock):
    aid = _submit_pricing(p)["approval_id"]
    with pytest.raises(ApprovalError):
        p.approvals.decide(aid, actor=users["u-country-manager"], decision="reject")
    clock.advance(minutes=1201)
    assert p.approvals.tick()["escalated"] == 1
    assert p.approvals.get(aid)["state"] == "escalated"
    clock.advance(minutes=300)
    assert p.approvals.tick()["expired"] == 1
    with pytest.raises(ApprovalError):
        p.approvals.decide(aid, actor=users["u-country-manager"], decision="approve")


def test_rule_reviewer_flags_unsourced_numbers_on_outbound(p):
    rv = p.approvals.reviewer.review("send:dealer_message", "D1023",
                                     {"text": "X6851 now NGN 175,000, guaranteed delivery. D1088 got the same."},
                                     ["X6851 price NGN 185,000"])
    assert rv["level"] == "reject"
    msgs = " ".join(i["msg"] for i in rv["issues"])
    assert "175000" in msgs and "承诺" in msgs and "D1088" in msgs
    ok = p.approvals.reviewer.review("send:dealer_message", "D1023", {"text": "X6851 NGN 185,000",
                                     "evidence_refs": ["kb:x"]}, ["X6851 price NGN 185,000"])
    assert ok["level"] == "low"


def test_bus_whitelist(p):
    ref = p.artifacts.put("market-intel", "competitor_prices", [{"x": 1}])
    ok = p.proxy.call("market-intel", "bus.send", {"to_bot": "price-sentinel", "intent": "competitor_price_event",
                                                   "payload_ref": ref}, trace_id="tb")
    assert ok["status"] == "ok"
    bad = p.proxy.call("market-intel", "bus.send", {"to_bot": "biz-review", "intent": "competitor_price_event",
                                                    "payload_ref": ref}, trace_id="tb")
    assert bad["status"] == "denied"
    msgs = p.proxy.call("price-sentinel", "bus.read", {}, trace_id="tc")["result"]["messages"]
    assert len(msgs) == 1 and msgs[0]["data"] == [{"x": 1}]


def test_notify_only_to_requester_for_manual_tasks(p, users):
    staff = users["u-staff-lagos-tecno"]
    r = p.proxy.call("biz-review", "notify.notify_internal", {"title": "x", "body": "y", "to": "group:ng-management"},
                     trace_id="tn", requester=staff["id"], requester_scope=staff["scope"])
    assert r["status"] == "denied"
    r = p.proxy.call("biz-review", "notify.notify_internal", {"title": "x", "body": "y"}, trace_id="tn",
                     requester=staff["id"], requester_scope=staff["scope"])
    assert r["result"]["to"] == f"user:{staff['id']}"

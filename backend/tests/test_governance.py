"""策略、作用域、审计哈希链、脱敏、记忆拦截。"""
import pytest

from app.gateway.redact import redact
from app.memory import MemoryRejected
from app.policy import intersect


def test_policy_priority(p):
    assert p.policy.decide("read:sales").tier == "auto"
    assert p.policy.decide("send:dealer_message").tier == "approval"
    assert p.policy.decide("handle:complaint").tier == "handoff"
    assert p.policy.decide("rebate:apply").tier == "forbidden"
    d = p.policy.decide("unknown:thing")
    assert d.tier == "approval" and d.unmatched


def test_scope_intersection():
    bot = {"country": ["NG"]}
    staff = {"country": ["NG"], "region": ["Lagos"], "brand": ["TECNO"]}
    assert intersect(bot, staff) == {"country": ["NG"], "region": ["Lagos"], "brand": ["TECNO"]}
    assert intersect({"region": ["North"]}, {"region": ["Lagos"]}) is None
    assert intersect(bot, None) == bot


def test_audit_chain_detects_tampering(p):
    for i in range(5):
        p.audit.append("test.event", bot_id="market-intel", n=i)
    assert p.audit.verify()["ok"]
    p.db.execute("UPDATE audit_events SET payload='{\"n\": 99}' WHERE seq=3")
    v = p.audit.verify()
    assert not v["ok"] and v["broken_at_seq"] == 3


def test_redaction():
    text, mapping, hits = redact("call 08031234567 or +234 803 123 4567, mail a.b@x.com, NIN 12345678901")
    assert "<PHONE_1>" in text and "<EMAIL_1>" in text and "<NIN_1>" in text
    assert hits["PHONE"] == 2 and "08031234567" not in text


@pytest.mark.parametrize("content", [
    "X6851 在拉各斯售价 NGN 185,000",
    "库存还有 1200 台",
    "以后都给这个经销商打 9 折",
    "联系人电话 08031234567",
])
def test_memory_rejects_facts_pii_instructions(p, content):
    with pytest.raises(MemoryRejected):
        p.memory.propose(namespace="bot/market-intel", type="preference", content=content)


def test_memory_lifecycle_and_namespace_isolation(p):
    m = p.memory.propose(namespace="bot/market-intel", type="preference", content="国家经理希望早报先写结论")
    assert m["status"] == "active"
    method = p.memory.propose(namespace="bot/market-intel", type="method", content="动销对比统一用 7 日移动平均")
    assert method["status"] == "proposed"  # 方法类须运营确认
    p.memory.propose(namespace="dealer/D1088", type="counterpart_preference", content="偏好豪萨语")
    got = p.memory.recall(["bot/market-intel", "dealer/D1023"], "早报 结论")
    assert all(r["namespace"] != "dealer/D1088" for r in got)
    p.memory.edit(m["memory_id"], actor="u-agent-ops", reason="改格式", content="国家经理希望早报先写结论、不超过 10 条")
    p.memory.delete(m["memory_id"], actor="u-agent-ops", reason="不再需要", hard=True)
    hist = p.memory.history(m["memory_id"])
    assert len(hist) == 3 and all(h["content"] == "[已删除]" for h in hist)

"""一线日报员：促销员 WhatsApp 报数（多语言随手写）→ 识别 → 入库 → 回执 → 汇总 / 未报提醒 / 进晚会材料。"""
from fastapi.testclient import TestClient

from app.field_reports import parse_rules
from app.main import create_app

CHIOMA, TUNDE, RINA = "2348100000011", "2348100000012", "6281200000021"


def units(text, country="NG"):
    return {i["model"]: i["units"] for i in parse_rules(text, country)}


def test_rule_parsing_variants():
    assert units("Today: Spark X6851 x3, Pop 9 x2") == {"Spark X6851": 3, "Pop 9": 2}
    assert units("I sell 3 spark and 1 camon 40 today") == {"Spark X6851": 3, "Camon 40": 1}
    assert units("今天卖了 4 台 Pop 9，Hot 50 2台") == {"Pop 9": 4, "Hot 50": 2}
    assert units("Jual 2 Hot 60i, GT 30 x5, spark 40: 1", "ID") == {"Hot 60i": 2, "GT 30": 5, "Spark 40": 1}
    assert units("Spark 40 belum ada yang beli", "ID") == {}            # 型号名里的数字不当台数
    assert units("no sales today") == {}


def test_whatsapp_report_flow(p):
    c = TestClient(create_app(p))
    h = {"X-User-Id": "u-lead-lagos"}
    r = c.post("/api/wa/simulate", json={"phone": CHIOMA, "text": "Spark x3, Pop 9 x2"}, headers=h).json()
    assert r["status"] == "field_report" and r["parsed"] == 2 and r["total_today"] == 5
    c.post("/api/wa/simulate", json={"phone": CHIOMA, "text": "1 more camon 40"}, headers=h)
    conv = p.whatsapp.conversation(CHIOMA)
    out = [m["text"] for m in p.whatsapp.messages(conv["conversation_id"]) if m["direction"] == "out"]
    assert "Your total today: 6 units" in out[-1]                            # 即时回执，含今日累计
    bad = c.post("/api/wa/simulate", json={"phone": TUNDE, "text": "abeg wetin dey happen"}, headers=h).json()
    assert bad["parsed"] == 0 and "abeg send am" in [m["text"] for m in p.whatsapp.messages(bad["conversation_id"])][-1].lower()
    s = c.get("/api/field-reports", params={"country": "NG", "region": "Lagos"}).json()
    assert s["total_units"] == 6 and s["reported"] == 1 and s["promoters"] == 3
    assert {m["name"] for m in s["missing"]} == {"Tunde", "Aisha"} and s["by_store"][0]["units"] == 6
    assert c.post("/api/field-reports/remind", json={"country": "NG", "region": "Lagos"}, headers={"X-User-Id": "u-staff-lagos-tecno"}).status_code == 403
    rm = c.post("/api/field-reports/remind", json={"country": "NG", "region": "Lagos"}, headers=h).json()
    # Tunde 24 小时内发过消息 → 可以提醒；Aisha 从没发过 → WhatsApp 规定只能发已审核模板，记录失败原因
    assert rm["sent"] == ["Tunde"] and rm["failed"][0]["name"] == "Aisha" and "24 小时" in rm["failed"][0]["reason"]
    # 印尼促销员走印尼语回执，数据按国家隔离
    c.post("/api/wa/simulate", json={"phone": RINA, "text": "Hot 60i 2"}, headers=h)
    assert c.get("/api/field-reports", params={"country": "ID"}).json()["total_units"] == 2
    assert c.get("/api/field-reports", params={"country": "NG"}).json()["total_units"] == 6
    assert "Diterima" in p.whatsapp.messages(p.whatsapp.conversation(RINA)["conversation_id"])[-1]["text"]
    # 经销商号码不受影响，仍走经销商助理
    assert p.field.text_for_review("NG", ["Lagos"]).startswith("一线促销员今日上报：1/3 人已报，合计 6 台")


def test_tool_and_review_material(p, clock):
    from tests.test_rhythm import _kpi
    p.whatsapp.receive(CHIOMA, "Spark x4", simulated=True)
    k = _kpi(p)
    clock.advance(hours=2)
    p.rhythm.tick()
    seen = {}
    real = p.gateway.chat

    def spy(bot_id, messages, **kw):
        if "复盘会材料" in messages[0]["content"]:
            seen["user"] = messages[1]["content"]
        return real(bot_id, messages, **kw)
    p.gateway.chat = spy
    clock.advance(hours=11)
    p.rhythm.prepare_review(k["kpi_id"])
    assert "一线促销员今日上报" in seen["user"] and "合计 4 台" in seen["user"]

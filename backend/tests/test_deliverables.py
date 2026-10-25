"""交付卡片：解析、Markdown 降级、HTML 转义与可读性约束、执行器与总调度接入。"""
import json

from fastapi.testclient import TestClient

from app.deliverables import card_to_html, card_to_markdown, parse_card
from app.main import create_app

RAW = {"title": "拉各斯动销周报", "headline": "Spark X6851 周环比 **-26.7%**，是最大缺口",
       "metrics": [{"label": "日均销量", "value": "295 台", "delta": "-13%", "trend": "down"}],
       "sections": [{"heading": "原因", "bullets": ["竞品降价【推断】", "<script>alert(1)</script>"]}],
       "table": {"columns": ["型号", "环比"], "rows": [["X6851", "-26.7%"]]},
       "actions": [{"owner": "价格哨兵", "action": "提交促销方案", "due": "周三"}], "risks": ["数据截至昨天"],
       "confidence": "中", "sources": ["sales-data"]}


def test_parse_markdown_and_html_are_safe():
    c = parse_card("好的：\n```json\n" + json.dumps(RAW, ensure_ascii=False) + "\n```")
    assert c and c["metrics"][0]["trend"] == "down" and c["table"]["rows"][0][0] == "X6851"
    md = card_to_markdown(c)
    assert "**结论**" in md and "| 型号 | 环比 |" in md
    h = card_to_html(c, bot_name="价格动销哨兵", bot_title="Pricing", color="#dc2626", when="2026-10-24 09:00",
                     model="m", goal="g", trace_id="trc")
    assert "<script>" not in h and "&lt;script&gt;" in h and "<b>-26.7%</b>" in h and "推断</span>" in h
    assert "font:14.5px" in h and "@media print" in h                      # 可读性：正文字号、打印
    assert parse_card("不是 JSON") is None and parse_card('{"x": 1}') is None


def test_runner_and_coordinator_deliver_cards(p, monkeypatch):
    real = p.gateway.chat

    def fake(bot_id, messages, **kw):
        if "【交付格式】" in messages[0]["content"]:
            return {"content": json.dumps(RAW, ensure_ascii=False), "model": "fake", "cost_usd": 0.0,
                    "input_tokens": 1, "output_tokens": 1, "fallback_from": None}
        return real(bot_id, messages, **kw)
    monkeypatch.setattr(p.gateway, "chat", fake)
    t = p.tasks.create(bot_id="chief-of-staff", goal="看看竞品调价和动销", source="manual", requester="u-country-manager",
                       requester_scope={"country": ["NG"]})
    p.runner.run(t["task_id"])
    t = p.tasks.get(t["task_id"])
    assert t["outputs"][0]["kind"] == "card" and "**结论**" in t["outputs"][0]["content"]
    kid = p.tasks.children(t["task_id"])[0]
    assert kid["outputs"][0]["card"]["title"] == "拉各斯动销周报"
    c = TestClient(create_app(p))
    r = c.get(f"/api/tasks/{kid['task_id']}/outputs/0/html")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "default-src 'none'" in r.headers["content-security-policy"]
    assert c.get(f"/api/tasks/{kid['task_id']}/outputs/5/html").status_code == 404


def test_charts_dashboard_and_tolerant_json():
    raw = dict(RAW, charts=[{"type": "pie", "title": "型号占比", "unit": "台", "data": [{"label": "Spark", "value": "1,196"}, {"label": "Pop", "value": 300}]},
                            {"type": "pie", "title": "环比", "data": [{"label": "A", "value": -5}, {"label": "B", "value": 3}]}])
    text = json.dumps(raw, ensure_ascii=False).replace("X6851", "X6851\t1,196")   # 字符串里混入制表符也能解析
    c = parse_card(text)
    assert c and c["charts"][0]["type"] == "pie" and c["charts"][0]["data"][0]["value"] == 1196
    assert c["charts"][1]["type"] == "bar"                                         # 含负数的饼图降级为条形图
    h = card_to_html(c, bot_name="b", bot_title="t", color="#dc2626", when="w", model=None, goal=None, trace_id=None)
    assert "<script" not in h and 'data-metric="1"' in h and 'data-slice="1"' in h and "<svg" in h
    assert "型号占比" in card_to_markdown(c)
    # 没给图表时从表格数值列自动生成
    auto = parse_card(json.dumps(dict(RAW, table={"columns": ["型号", "销量"], "rows": [["A", "120"], ["B", "80 台"]]}), ensure_ascii=False))
    assert auto["charts"][0]["auto"] and [x["value"] for x in auto["charts"][0]["data"]] == [120, 80]

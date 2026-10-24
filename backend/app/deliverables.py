"""交付卡片：成员的产出统一为结构化卡片 → 服务端渲染成独立 HTML（嵌入工作流 / 新窗口打开 / 下载）。

- 模型按 CARD_SPEC 输出 JSON；解析失败时退回普通 Markdown，不影响交付。
- 同时生成 Markdown 版本供下游（总调度汇总、通知正文、晨会复盘）读取。
- HTML 全部转义、不含脚本，可安全嵌入（iframe sandbox）。
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

CARD_SPEC = """

【交付格式】只输出一个 JSON 对象（不要代码块标记、不要其他文字），字段如下：
{"title": "交付标题（≤20字）",
 "headline": "一句话核心结论，带关键数字",
 "metrics": [{"label": "指标名", "value": "数值（带单位）", "delta": "变化，如 -26.7%（可空）", "trend": "up|down|flat", "note": "补充（可空）"}],
 "sections": [{"heading": "小标题", "bullets": ["要点（每条≤60字，可用 **加粗**）"]}],
 "table": {"columns": ["列1", "列2"], "rows": [["值", "值"]]},
 "actions": [{"owner": "谁", "action": "做什么", "due": "何时"}],
 "risks": ["风险或注意事项"],
 "confidence": "高|中|低",
 "sources": ["数据来源"]}
要求：metrics 0-4 个；sections 1-4 段；table 可省略，最多 8 行；actions 1-5 条；数字只能来自给定数据，推断在文字中标注【推断】。"""

TRENDS = {"up", "down", "flat"}


def _s(v: Any, n: int = 400) -> str:
    return str(v if v is not None else "").strip()[:n]


def parse_card(text: str | None) -> dict | None:
    """从模型输出中解析交付卡片；字段做类型与长度约束。"""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict) or not (d.get("headline") or d.get("sections")):
        return None
    card: dict = {"title": _s(d.get("title"), 60) or "交付", "headline": _s(d.get("headline"), 300)}
    card["metrics"] = [{"label": _s(x.get("label"), 30), "value": _s(x.get("value"), 30), "delta": _s(x.get("delta"), 20),
                        "trend": x.get("trend") if x.get("trend") in TRENDS else "flat", "note": _s(x.get("note"), 60)}
                       for x in (d.get("metrics") or []) if isinstance(x, dict) and x.get("label")][:4]
    card["sections"] = [{"heading": _s(x.get("heading"), 40),
                         "bullets": [_s(b, 300) for b in (x.get("bullets") or []) if _s(b)][:8]}
                        for x in (d.get("sections") or []) if isinstance(x, dict)][:5]
    tb = d.get("table")
    if isinstance(tb, dict) and isinstance(tb.get("columns"), list) and isinstance(tb.get("rows"), list):
        cols = [_s(c, 30) for c in tb["columns"]][:8]
        rows = [[_s(c, 80) for c in r][:len(cols)] for r in tb["rows"] if isinstance(r, list)][:10]
        card["table"] = {"columns": cols, "rows": rows} if cols and rows else None
    else:
        card["table"] = None
    card["actions"] = [{"owner": _s(x.get("owner"), 30), "action": _s(x.get("action"), 200), "due": _s(x.get("due"), 30)}
                       for x in (d.get("actions") or []) if isinstance(x, dict) and x.get("action")][:6]
    card["risks"] = [_s(r, 200) for r in (d.get("risks") or []) if _s(r)][:5]
    card["confidence"] = d.get("confidence") if d.get("confidence") in ("高", "中", "低") else None
    card["sources"] = [_s(s, 80) for s in (d.get("sources") or []) if _s(s)][:5]
    return card


def card_to_markdown(c: dict) -> str:
    out = [f"## {c['title']}", f"**结论**：{c['headline']}"]
    if c["metrics"]:
        out.append("\n".join(f"- {m['label']}：{m['value']}" + (f"（{m['delta']}）" if m["delta"] else "") for m in c["metrics"]))
    for s in c["sections"]:
        out.append(f"### {s['heading']}\n" + "\n".join(f"- {b}" for b in s["bullets"]))
    if c.get("table"):
        t = c["table"]
        out.append("| " + " | ".join(t["columns"]) + " |\n|" + "---|" * len(t["columns"]) + "\n"
                   + "\n".join("| " + " | ".join(r) + " |" for r in t["rows"]))
    if c["actions"]:
        out.append("### 下一步\n" + "\n".join(f"- {a['owner'] + '：' if a['owner'] else ''}{a['action']}"
                                              + (f"（{a['due']}）" if a["due"] else "") for a in c["actions"]))
    if c["risks"]:
        out.append("### 风险\n" + "\n".join(f"- {r}" for r in c["risks"]))
    return "\n\n".join(out)


def _rich(text: str) -> str:
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"【(推断|预估|假设)】", r'<span class="tag-infer">\1</span>', t)


def _initials(name: str) -> str:
    base = name.split("·")[0].strip()
    return base[:2] if re.search(r"[一-鿿]", base) else "".join(w[0] for w in base.split()[:2]).upper()


def card_to_html(c: dict, *, bot_name: str, bot_title: str, color: str, when: str, model: str | None,
                 goal: str | None, trace_id: str | None) -> str:
    e = html.escape
    color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color or "") else "#4f46e5"
    arrow = {"up": "▲", "down": "▼", "flat": "■"}
    metrics = "".join(
        f'<div class="m"><div class="ml">{e(m["label"])}</div><div class="mv">{e(m["value"])}</div>'
        + (f'<div class="md {m["trend"]}">{arrow[m["trend"]]} {e(m["delta"])}</div>' if m["delta"] else "")
        + (f'<div class="mn">{e(m["note"])}</div>' if m["note"] else "") + "</div>"
        for m in c["metrics"])
    sections = "".join(
        f'<section><h3>{e(s["heading"])}</h3><ul>' + "".join(f"<li>{_rich(b)}</li>" for b in s["bullets"]) + "</ul></section>"
        for s in c["sections"] if s["bullets"])
    table = ""
    if c.get("table"):
        t = c["table"]
        table = ('<div class="tw"><table><thead><tr>' + "".join(f"<th>{e(x)}</th>" for x in t["columns"]) + "</tr></thead><tbody>"
                 + "".join("<tr>" + "".join(f"<td>{_rich(x)}</td>" for x in r) + "</tr>" for r in t["rows"]) + "</tbody></table></div>")
    actions = ""
    if c["actions"]:
        actions = '<section><h3>下一步</h3><div class="acts">' + "".join(
            f'<div class="act"><span class="chk"></span><div><div>{_rich(a["action"])}</div><div class="meta">'
            + (f'<span class="who">{e(a["owner"])}</span>' if a["owner"] else "") + (f'<span>{e(a["due"])}</span>' if a["due"] else "")
            + "</div></div></div>" for a in c["actions"]) + "</div></section>"
    risks = ('<div class="risk"><b>风险与注意</b><ul>' + "".join(f"<li>{_rich(r)}</li>" for r in c["risks"]) + "</ul></div>"
             if c["risks"] else "")
    conf = {"高": "good", "中": "warn", "低": "bad"}.get(c.get("confidence") or "", "")
    foot = " · ".join(x for x in [f"来源：{'、'.join(e(s) for s in c['sources'])}" if c["sources"] else "",
                                   f"模型 {e(model)}" if model else "", f"审计 {e(trace_id)}" if trace_id else ""] if x)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(c['title'])} · {e(bot_name)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font:14.5px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",Roboto,sans-serif;color:#0f172a;background:transparent}}
.card{{max-width:960px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;border-radius:14px;overflow:hidden;box-shadow:0 1px 2px rgba(15,23,42,.04)}}
.hd{{display:flex;gap:12px;align-items:center;padding:14px 18px;background:linear-gradient(135deg,{color}14,{color}05);border-bottom:1px solid #eef0f3;position:relative}}
.hd:before{{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:{color}}}
.av{{width:36px;height:36px;border-radius:10px;background:{color};color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12px;flex:none}}
.t{{font-size:16px;font-weight:700;line-height:1.3}}.sub{{color:#475569;font-size:12.5px}}
.conf{{margin-left:auto;font-size:12.5px;padding:2px 9px;border-radius:999px;background:#f1f5f9;color:#475569;white-space:nowrap}}
.conf.good{{background:#ecfdf5;color:#047857}}.conf.warn{{background:#fffbeb;color:#b45309}}.conf.bad{{background:#fef2f2;color:#b91c1c}}
.bd{{padding:16px 18px 6px}}
.hl{{font-size:15.5px;line-height:1.65;font-weight:600;padding:10px 14px;border-radius:10px;background:{color}0d;border:1px solid {color}26;margin-bottom:14px}}
.ms{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px}}
.m{{border:1px solid #eef0f3;border-radius:10px;padding:10px 12px;background:#fafbfc}}
.ml{{color:#475569;font-size:12.5px}}.mv{{font-size:22px;font-weight:700;letter-spacing:-.02em;line-height:1.25}}
.md{{font-size:12.5px;font-weight:600}}.md.up{{color:#059669}}.md.down{{color:#dc2626}}.md.flat{{color:#64748b}}.mn{{color:#64748b;font-size:12.5px}}
section{{margin:0 0 12px}}h3{{font-size:14px;margin:0 0 6px;color:#1e293b;letter-spacing:.02em}}
ul{{margin:0;padding-left:18px}}li{{margin:4px 0;max-width:72em}}li::marker{{color:{color}}}
.tw{{overflow-x:auto;margin:0 0 14px;border:1px solid #eef0f3;border-radius:10px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;line-height:1.55}}th{{background:#f8fafc;text-align:left;font-weight:600;color:#475569;padding:8px 10px;border-bottom:1px solid #e5e7eb}}
td{{padding:8px 10px;border-bottom:1px solid #f1f5f9;vertical-align:top;word-break:break-word;min-width:5em}}tr:last-child td{{border-bottom:0}}tbody tr:nth-child(even){{background:#fcfcfd}}
.acts{{display:grid;gap:6px}}.act{{display:flex;gap:10px;align-items:flex-start;padding:8px 10px;border:1px solid #eef0f3;border-radius:10px}}
.chk{{width:14px;height:14px;border:2px solid {color};border-radius:4px;margin-top:4px;flex:none}}
.meta{{display:flex;gap:8px;font-size:12.5px;color:#475569}}.who{{color:{color};font-weight:600}}
.risk{{background:#fff7ed;border:1px solid #fed7aa;color:#7c2d12;border-radius:10px;padding:10px 14px;margin:0 0 12px;font-size:14px}}.risk ul{{margin-top:4px}}
.tag-infer{{font-size:12px;background:#eef2ff;color:#4338ca;border-radius:4px;padding:0 4px;margin:0 2px}}
.ft{{padding:10px 18px;border-top:1px solid #eef0f3;color:#64748b;font-size:12.5px;background:#fafbfc;word-break:break-all}}
.goal{{color:#475569;font-size:13px;margin:-6px 0 12px}}
@media (max-width:560px){{.bd{{padding:14px}}.mv{{font-size:19px}}.hd{{padding:12px 14px}}}}
@media print{{body{{background:#fff}}.card{{box-shadow:none;border:0}}.m,.act,.tw{{break-inside:avoid}}}}
</style></head><body><div class="card">
<div class="hd"><div class="av">{e(_initials(bot_name))}</div><div><div class="t">{e(c['title'])}</div><div class="sub">{e(bot_name)} · {e(bot_title or '')} · {e(when)}</div></div>
{f'<span class="conf {conf}">可信度 {e(c["confidence"])}</span>' if c.get("confidence") else ''}</div>
<div class="bd">{f'<div class="goal">任务：{e(goal)}</div>' if goal else ''}<div class="hl">{_rich(c['headline'])}</div>
{f'<div class="ms">{metrics}</div>' if metrics else ''}{sections}{table}{actions}{risks}</div>
{f'<div class="ft">{foot}</div>' if foot else ''}</div></body></html>"""

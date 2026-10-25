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
 "charts": [{"type": "pie|bar", "title": "图表标题", "unit": "单位", "data": [{"label": "名称", "value": 123}]}],
 "sections": [{"heading": "小标题", "bullets": ["要点（每条≤60字，可用 **加粗**）"]}],
 "table": {"columns": ["列1", "列2"], "rows": [["值", "值"]]},
 "actions": [{"owner": "谁", "action": "做什么", "due": "何时"}],
 "risks": ["风险或注意事项"],
 "confidence": "高|中|低",
 "sources": ["数据来源"]}
要求：metrics 0-4 个；charts 0-2 个——数据里有占比 / 构成就给 pie（≤6 项），有多项对比就给 bar（≤8 项，可含负数如环比），value 必须是数字且来自给定数据；
sections 1-4 段；table 可省略，最多 8 行；actions 1-5 条；数字只能来自给定数据，推断在文字中标注【推断】；字符串里不要用制表符。"""

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
    raw = m.group(0)
    d = None
    for candidate in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):  # 容忍控制字符（制表符等）与尾逗号
        try:
            d = json.loads(candidate, strict=False)
            break
        except ValueError:
            continue
    if d is None:
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
    card["charts"] = _charts(d.get("charts")) or _auto_chart(card["table"])
    card["actions"] = [{"owner": _s(x.get("owner"), 30), "action": _s(x.get("action"), 200), "due": _s(x.get("due"), 30)}
                       for x in (d.get("actions") or []) if isinstance(x, dict) and x.get("action")][:6]
    card["risks"] = [_s(r, 200) for r in (d.get("risks") or []) if _s(r)][:5]
    card["confidence"] = d.get("confidence") if d.get("confidence") in ("高", "中", "低") else None
    card["sources"] = [_s(s, 80) for s in (d.get("sources") or []) if _s(s)][:5]
    return card


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.search(r"-?\d[\d,]*\.?\d*", str(v or ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _charts(raw: Any) -> list[dict]:
    out = []
    for c in (raw or [])[:2] if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        data = [{"label": _s(x.get("label"), 24), "value": _num(x.get("value"))} for x in (c.get("data") or []) if isinstance(x, dict)]
        data = [x for x in data if x["label"] and x["value"] is not None][:8]
        kind = c.get("type") if c.get("type") in ("pie", "donut", "bar") else "bar"
        if kind in ("pie", "donut") and (len(data) < 2 or any(x["value"] < 0 for x in data)):
            kind = "bar"
        if len(data) >= 2:
            out.append({"type": "pie" if kind == "donut" else kind, "title": _s(c.get("title"), 30) or "数据",
                        "unit": _s(c.get("unit"), 8), "data": data})
    return out


def _auto_chart(table: dict | None) -> list[dict]:
    """模型没给图表时，从表格里取第一列数值列自动生成条形图。"""
    if not table or len(table["rows"]) < 2:
        return []
    cols = table["columns"]
    for j in range(1, len(cols)):
        vals = [_num(r[j]) if j < len(r) else None for r in table["rows"]]
        if sum(v is not None for v in vals) >= max(2, int(len(vals) * 0.6)):
            data = [{"label": _s(r[0], 24), "value": v} for r, v in zip(table["rows"], vals) if v is not None][:8]
            return [{"type": "bar", "title": f"{cols[j]}（按{cols[0]}）", "unit": "", "data": data, "auto": True}]
    return []


def card_to_markdown(c: dict) -> str:
    out = [f"## {c['title']}", f"**结论**：{c['headline']}"]
    if c["metrics"]:
        out.append("\n".join(f"- {m['label']}：{m['value']}" + (f"（{m['delta']}）" if m["delta"] else "") for m in c["metrics"]))
    for ch in c.get("charts") or []:
        out.append(f"### {ch['title']}\n" + "\n".join(f"- {x['label']}：{x['value']:g}{ch['unit']}" for x in ch["data"]))
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


PALETTE = ["#0a84ff", "#5e5ce6", "#30d158", "#ff9f0a", "#ff375f", "#64d2ff", "#bf5af2", "#ffd60a"]


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 or v == int(v) else f"{v:,.1f}"


def _chart_html(ch: dict, ci: int) -> str:
    e = html.escape
    data, unit = ch["data"], e(ch.get("unit") or "")
    if ch["type"] == "pie":
        total = sum(x["value"] for x in data) or 1
        R, C = 70, 2 * 3.14159265 * 70
        acc, arcs, legend = 0.0, [], []
        for i, x in enumerate(data):
            frac = x["value"] / total
            col = PALETTE[i % len(PALETTE)]
            arcs.append(f'<circle class="arc" data-slice="1" data-chart="{e(ch["title"])}" data-label="{e(x["label"])}" '
                        f'data-value="{_fmt(x["value"])}{unit}" data-pct="{frac * 100:.1f}" cx="90" cy="90" r="{R}" fill="none" '
                        f'stroke="{col}" stroke-width="30" stroke-dasharray="{frac * C:.2f} {C:.2f}" stroke-dashoffset="{-acc * C:.2f}" '
                        f'style="--len:{frac * C:.2f};--d:{i * 0.12:.2f}s" transform="rotate(-90 90 90)"><title>{e(x["label"])}：{_fmt(x["value"])}{unit}（{frac * 100:.1f}%）</title></circle>')
            legend.append(f'<div class="lg" data-slice="1" data-chart="{e(ch["title"])}" data-label="{e(x["label"])}" '
                          f'data-value="{_fmt(x["value"])}{unit}" data-pct="{frac * 100:.1f}"><i style="background:{col}"></i>'
                          f'<span>{e(x["label"])}</span><b>{_fmt(x["value"])}{unit}</b><em>{frac * 100:.0f}%</em></div>')
            acc += frac
        body = (f'<div class="pie"><svg viewBox="0 0 180 180" width="180" height="180">{"".join(arcs)}'
                f'<text x="90" y="86" class="pie-total">{_fmt(total)}</text><text x="90" y="106" class="pie-sub">合计{unit}</text></svg>'
                f'<div class="legend">{"".join(legend)}</div></div>')
    else:
        mx = max(abs(x["value"]) for x in data) or 1
        neg = any(x["value"] < 0 for x in data)
        rows = []
        for i, x in enumerate(data):
            w = abs(x["value"]) / mx * (50 if neg else 100)
            left = (50 - w if x["value"] < 0 else 50) if neg else 0
            col = "#ff453a" if x["value"] < 0 else PALETTE[i % len(PALETTE)] if not neg else "#30d158"
            rows.append(f'<div class="br" data-slice="1" data-chart="{e(ch["title"])}" data-label="{e(x["label"])}" '
                        f'data-value="{_fmt(x["value"])}{unit}" style="--d:{i * 0.07:.2f}s"><span class="bl" title="{e(x["label"])}">{e(x["label"])}</span>'
                        f'<span class="bt">{"<b class=zero></b>" if neg else ""}<i style="left:{left:.1f}%;width:{w:.1f}%;background:{col}"></i></span>'
                        f'<span class="bv" style="color:{col if neg else "inherit"}">{_fmt(x["value"])}{unit}</span></div>')
        body = f'<div class="bars">{"".join(rows)}</div>'
    auto = '<span class="auto">由表格自动生成</span>' if ch.get("auto") else ""
    return f'<div class="chart rise" style="--i:{2 + ci}"><div class="ch-title">{e(ch["title"])}{auto}</div>{body}</div>'


def card_to_html(c: dict, *, bot_name: str, bot_title: str, color: str, when: str, model: str | None,
                 goal: str | None, trace_id: str | None, methods: list[str] | None = None) -> str:
    e = html.escape
    color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color or "") else "#4f46e5"
    arrow = {"up": "▲", "down": "▼", "flat": "■"}
    metrics = "".join(
        f'<div class="m rise {m["trend"]}" style="--i:{1 + i * 0.3:.1f}" data-metric="1" data-label="{e(m["label"])}" data-value="{e(m["value"])}" data-delta="{e(m["delta"])}">'
        f'<div class="ml">{e(m["label"])}</div><div class="mv">{e(m["value"])}</div>'
        + (f'<div class="md {m["trend"]}">{arrow[m["trend"]]} {e(m["delta"])}</div>' if m["delta"] else "")
        + (f'<div class="mn">{e(m["note"])}</div>' if m["note"] else "") + '<div class="go">深入分析 →</div></div>'
        for i, m in enumerate(c["metrics"]))
    charts = "".join(_chart_html(ch, i) for i, ch in enumerate(c.get("charts") or []))
    sections = "".join(
        f'<section class="rise" style="--i:4"><h3>{e(s["heading"])}</h3><ul>' + "".join(f"<li>{_rich(b)}</li>" for b in s["bullets"]) + "</ul></section>"
        for s in c["sections"] if s["bullets"])
    table = ""
    if c.get("table"):
        t = c["table"]
        table = ('<div class="tw rise" style="--i:5"><table><thead><tr>' + "".join(f"<th>{e(x)}</th>" for x in t["columns"]) + "</tr></thead><tbody>"
                 + "".join("<tr>" + "".join(f"<td>{_rich(x)}</td>" for x in r) + "</tr>" for r in t["rows"]) + "</tbody></table></div>")
    actions = ""
    if c["actions"]:
        actions = '<section class="rise" style="--i:6"><h3>下一步</h3><div class="acts">' + "".join(
            f'<div class="act" data-act="{i}" data-text="{e(a["action"])}" data-owner="{e(a["owner"])}" data-due="{e(a["due"])}">'
            f'<span class="num">{i + 1}</span><div style="flex:1"><div>{_rich(a["action"])}</div><div class="meta">'
            + (f'<span class="who">{e(a["owner"])}</span>' if a["owner"] else "") + (f'<span>{e(a["due"])}</span>' if a["due"] else "")
            + '</div></div><span class="go">继续推进 →</span></div>' for i, a in enumerate(c["actions"])) + "</div></section>"
    risks = ('<div class="risk rise" style="--i:7"><b>⚠ 风险与注意</b><ul>' + "".join(f"<li>{_rich(r)}</li>" for r in c["risks"]) + "</ul></div>"
             if c["risks"] else "")
    conf = {"高": "good", "中": "warn", "低": "bad"}.get(c.get("confidence") or "", "")
    foot = " · ".join(x for x in [f"来源：{'、'.join(e(s) for s in c['sources'])}" if c["sources"] else "",
                                   f"方法：{'、'.join(e(m) for m in methods)}" if methods else "",
                                   f"模型 {e(model)}" if model else "", f"审计 {e(trace_id)}" if trace_id else ""] if x)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(c['title'])} · {e(bot_name)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font:14.5px/1.7 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",Roboto,sans-serif;color:#1d1d1f;background:transparent;-webkit-font-smoothing:antialiased}}
.card{{max-width:980px;margin:0 auto;background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.04),0 10px 34px rgba(0,0,0,.08)}}
.hd{{position:relative;display:flex;gap:14px;align-items:center;padding:20px 22px 22px;color:#fff;overflow:hidden;
  background:radial-gradient(120% 140% at 0% 0%,{color} 0%,{color}cc 30%,#14162b 100%)}}
.hd:after{{content:"";position:absolute;right:-60px;top:-80px;width:260px;height:260px;border-radius:50%;background:radial-gradient(circle,{color}88,transparent 70%);filter:blur(10px);animation:glow 6s ease-in-out infinite}}
@keyframes glow{{50%{{transform:translate(-30px,20px) scale(1.15)}}}}
.av{{width:42px;height:42px;border-radius:12px;background:rgba(255,255,255,.18);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;flex:none;border:1px solid rgba(255,255,255,.25)}}
.t{{font-size:19px;font-weight:750;letter-spacing:-.015em;line-height:1.3;position:relative}}.sub{{color:rgba(255,255,255,.75);font-size:12.5px;position:relative}}
.conf{{margin-left:auto;font-size:12px;padding:4px 11px;border-radius:999px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.25);white-space:nowrap;position:relative}}
.bd{{padding:18px 22px 8px}}
.goal{{color:#6e6e73;font-size:13px;margin:0 0 12px}}
.hl{{font-size:16px;line-height:1.6;font-weight:650;padding:14px 16px;border-radius:14px;background:linear-gradient(120deg,{color}14,{color}06);border:1px solid {color}2a;margin-bottom:16px}}
.ms{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}}
.m{{position:relative;border-radius:16px;padding:12px 14px 12px;background:#fbfbfd;border:1px solid rgba(0,0,0,.06);overflow:hidden;transition:transform .2s,box-shadow .2s}}
.m:before{{content:"";position:absolute;left:0;top:0;right:0;height:3px;background:linear-gradient(90deg,{color},#5e5ce6)}}
.m.down:before{{background:linear-gradient(90deg,#ff453a,#ff9f0a)}}.m.up:before{{background:linear-gradient(90deg,#30d158,#64d2ff)}}
.ml{{color:#6e6e73;font-size:12.5px}}.mv{{font-size:26px;font-weight:800;letter-spacing:-.03em;line-height:1.2;margin-top:2px}}
.md{{font-size:12.5px;font-weight:700;margin-top:2px}}.md.up{{color:#248a3d}}.md.down{{color:#d70015}}.md.flat{{color:#6e6e73}}.mn{{color:#86868b;font-size:12px}}
.go{{display:none;font-size:12px;font-weight:700;color:{color};margin-top:4px}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-bottom:16px}}
.chart{{border-radius:16px;border:1px solid rgba(0,0,0,.06);padding:14px 16px;background:linear-gradient(180deg,#fff,#fbfbfd)}}
.ch-title{{font-weight:700;font-size:13.5px;margin-bottom:8px;display:flex;align-items:center;gap:8px}}.auto{{font-size:11px;font-weight:500;color:#86868b;background:#f2f2f7;border-radius:6px;padding:0 6px}}
.pie{{display:flex;gap:14px;align-items:center;flex-wrap:wrap}}
.arc{{animation:draw 1s cubic-bezier(.3,.9,.3,1) both;animation-delay:var(--d);transition:stroke-width .2s,opacity .2s}}
@keyframes draw{{from{{stroke-dasharray:0 440}}}}
.pie svg:hover .arc{{opacity:.45}}.pie svg .arc:hover{{opacity:1;stroke-width:36}}
.pie-total{{font-size:22px;font-weight:800;text-anchor:middle;fill:#1d1d1f}}.pie-sub{{font-size:11px;text-anchor:middle;fill:#86868b}}
.legend{{flex:1;min-width:150px;display:grid;gap:4px}}
.lg{{display:flex;align-items:center;gap:8px;font-size:12.5px;padding:4px 8px;border-radius:9px;transition:background .15s}}
.lg i{{width:10px;height:10px;border-radius:3px;flex:none}}.lg span{{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.lg em{{font-style:normal;color:#86868b;width:34px;text-align:right}}
.bars{{display:grid;gap:7px}}
.br{{display:grid;grid-template-columns:minmax(70px,30%) 1fr 76px;gap:10px;align-items:center;font-size:12.5px;padding:3px 6px;border-radius:9px;transition:background .15s}}
.bl{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#3a3a3c}}.bv{{text-align:right;font-weight:700;font-variant-numeric:tabular-nums}}
.bt{{position:relative;height:12px;background:#f2f2f7;border-radius:7px;overflow:hidden}}
.bt i{{position:absolute;top:0;bottom:0;border-radius:7px;animation:grow .9s cubic-bezier(.3,.9,.3,1) both;animation-delay:var(--d);transform-origin:left}}
.bt .zero{{position:absolute;left:50%;top:-2px;bottom:-2px;width:1.5px;background:#c7c7cc;z-index:1}}
@keyframes grow{{from{{transform:scaleX(0)}}}}
section{{margin:0 0 14px}}h3{{font-size:14px;margin:0 0 6px;color:#1d1d1f;letter-spacing:.01em;display:flex;align-items:center;gap:8px}}
h3:before{{content:"";width:4px;height:14px;border-radius:2px;background:{color}}}
ul{{margin:0;padding-left:20px}}li{{margin:4px 0;max-width:72em}}li::marker{{color:{color}}}
.tw{{overflow-x:auto;margin:0 0 16px;border:1px solid rgba(0,0,0,.06);border-radius:14px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;line-height:1.55}}th{{background:#f5f5f7;text-align:left;font-weight:650;color:#3a3a3c;padding:9px 12px;border-bottom:1px solid rgba(0,0,0,.06)}}
td{{padding:9px 12px;border-bottom:1px solid rgba(0,0,0,.04);vertical-align:top;word-break:break-word;min-width:5em}}tr:last-child td{{border-bottom:0}}tbody tr:hover{{background:#fafafc}}
.acts{{display:grid;gap:8px}}.act{{display:flex;gap:12px;align-items:flex-start;padding:10px 12px;border-radius:14px;background:#fbfbfd;border:1px solid rgba(0,0,0,.06);transition:all .18s}}
.num{{width:22px;height:22px;border-radius:50%;background:{color};color:#fff;font-size:12px;font-weight:800;display:flex;align-items:center;justify-content:center;flex:none;margin-top:1px}}
.meta{{display:flex;gap:8px;font-size:12.5px;color:#6e6e73}}.who{{color:{color};font-weight:650}}
.act .go{{align-self:center;white-space:nowrap}}
.risk{{background:linear-gradient(120deg,#fff4e5,#fffaf2);border:1px solid #ffd8a8;color:#7c3a00;border-radius:14px;padding:12px 16px;margin:0 0 14px;font-size:14px}}.risk ul{{margin-top:4px}}
.tag-infer{{font-size:11.5px;background:#eef2ff;color:#4338ca;border-radius:5px;padding:0 5px;margin:0 2px}}
.ft{{padding:11px 22px;border-top:1px solid rgba(0,0,0,.05);color:#86868b;font-size:12px;background:#fbfbfd;word-break:break-all}}
.rise{{animation:rise .55s cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--i,0)*80ms)}}
@keyframes rise{{from{{opacity:0;transform:translateY(10px)}}}}
/* 嵌入控制台时（外层挂了点击）才显示可点击效果 */
.interactive [data-metric],.interactive [data-slice],.interactive .act{{cursor:pointer}}
.interactive .m:hover{{transform:translateY(-3px);box-shadow:0 12px 26px {color}2e;border-color:{color}55}}.interactive .m:hover .go{{display:block}}
.interactive .lg:hover,.interactive .br:hover{{background:{color}12}}
.interactive .act:hover{{border-color:{color};background:{color}0d;transform:translateX(3px)}}.interactive .act .go{{display:inline}}
@media (max-width:560px){{.bd{{padding:14px}}.mv{{font-size:21px}}.hd{{padding:16px}}}}
@media print{{.card{{box-shadow:none}}.rise,.arc,.bt i{{animation:none}}.m,.act,.tw,.chart{{break-inside:avoid}}}}
</style></head><body><div class="card">
<div class="hd"><div class="av">{e(_initials(bot_name))}</div><div style="position:relative"><div class="t">{e(c['title'])}</div><div class="sub">{e(bot_name)} · {e(bot_title or '')} · {e(when)}</div></div>
{f'<span class="conf {conf}">可信度 {e(c["confidence"])}</span>' if c.get("confidence") else ''}</div>
<div class="bd">{f'<div class="goal">任务：{e(goal)}</div>' if goal else ''}<div class="hl rise">{_rich(c['headline'])}</div>
{f'<div class="ms">{metrics}</div>' if metrics else ''}{f'<div class="charts">{charts}</div>' if charts else ''}{sections}{table}{actions}{risks}</div>
{f'<div class="ft">{foot}</div>' if foot else ''}</div></body></html>"""

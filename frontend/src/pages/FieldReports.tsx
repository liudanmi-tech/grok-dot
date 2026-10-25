import { useState } from "react";
import { BellRing, MessageCircle, Send } from "lucide-react";
import { fmtTime, post } from "../api";
import { usePoll } from "../hooks";
import { MARKETS } from "../ui";

const SAMPLES: Record<string, string[]> = {
  NG: ["Today: Spark x3, Pop 9 x2", "I sell 2 camon 40 and 1 hot 50", "Abeg 4 spark don go, 1 note 40"],
  ID: ["Jual 2 Hot 60i, GT 30 x3", "Hari ini Spark 40 x4, Note 50 x1", "3 unit POVA 7"],
};

// 一线日报：促销员 WhatsApp 报数 → 自动识别入库 → 主管看板（已报 / 未报、门店与型号排行、原始上报）
export default function FieldReports({ me }: any) {
  const [country, setCountry] = useState("NG");
  const [region, setRegion] = useState("");
  const q = `/field-reports?country=${country}${region ? `&region=${encodeURIComponent(region)}` : ""}`;
  const { data: s, reload } = usePoll<any>(q, 3000);
  const wa = usePoll<any>("/wa/status", 0);
  const [remind, setRemind] = useState<any>(null);
  const canRemind = me?.roles?.some((r: string) => ["supervisor", "country_manager", "channel_manager", "agent_ops"].includes(r));
  const promoters = (wa.data?.promoters || []).filter((p: any) => p.country === country && (!region || p.region === region));
  const maxStore = Math.max(1, ...(s?.by_store || []).map((x: any) => x.units));
  const maxModel = Math.max(1, ...(s?.by_model || []).map((x: any) => x.units));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>一线日报</h1>
          <p>驻店促销员在 WhatsApp 随手报数（英语 / 皮钦语 / 印尼语 / 中文都行），一线日报员自动识别型号和台数、即时回执；当天第一条即打卡，数据进晚会复盘。</p>
        </div>
        <div className="row">
          <div className="seg">{Object.entries(MARKETS).map(([c, m]) => (
            <button key={c} className={country === c ? "on" : ""} onClick={() => { setCountry(c); setRegion(""); }}>{m.name}</button>))}</div>
          <select value={region} onChange={(e) => setRegion(e.target.value)}>
            <option value="">全部区域</option>{MARKETS[country].regions.map((r) => <option key={r}>{r}</option>)}</select>
        </div>
      </div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="card kpi"><div className="l">今日上报</div><div className="v">{s?.total_units ?? "—"}<span className="muted"> 台</span></div><div className="muted small">{s?.day} · {s?.reports ?? 0} 条</div></div>
        <div className="card kpi"><div className="l">已报 / 应报</div><div className="v">{s?.reported ?? 0}<span className="muted"> / {s?.promoters ?? 0} 人</span></div>
          <div className="bar" style={{ marginTop: 8 }}><i style={{ width: `${s?.promoters ? (100 * s.reported) / s.promoters : 0}%` }} /></div></div>
        <div className="card kpi"><div className="l">未报</div><div className="v" style={{ color: s?.missing?.length ? "var(--warn)" : undefined }}>{s?.missing?.length ?? 0}<span className="muted"> 人</span></div>
          <div className="muted small ellipsis">{(s?.missing || []).map((m: any) => m.name).join("、") || "全部已报 ✓"}</div></div>
        <div className="card kpi"><div className="l">最畅销型号</div><div className="v" style={{ fontSize: 20 }}>{s?.by_model?.[0]?.model || "—"}</div><div className="muted small">{s?.by_model?.[0] ? `${s.by_model[0].units} 台` : "暂无上报"}</div></div>
      </div>

      <div className="split">
        <div className="stack">
          <div className="grid g2">
            <div className="card">
              <div className="card-head"><h2>门店排行</h2></div>
              {!s?.by_store?.length && <div className="muted">今天还没有上报。</div>}
              {(s?.by_store || []).map((x: any) => (
                <div key={x.store} className="rank-row"><span className="ellipsis">{x.store}<em> · {x.promoter}</em></span>
                  <span className="rank-bar"><i style={{ width: `${(100 * x.units) / maxStore}%` }} /></span><b>{x.units}</b></div>
              ))}
            </div>
            <div className="card">
              <div className="card-head"><h2>型号排行</h2></div>
              {!s?.by_model?.length && <div className="muted">今天还没有上报。</div>}
              {(s?.by_model || []).map((x: any) => (
                <div key={x.model} className="rank-row"><span className="ellipsis">{x.model}</span>
                  <span className="rank-bar green"><i style={{ width: `${(100 * x.units) / maxModel}%` }} /></span><b>{x.units}</b></div>
              ))}
            </div>
          </div>
          <div className="card">
            <div className="card-head"><h2>上报明细</h2><span className="muted small">原话 → 识别结果（规则识别 / 模型识别）</span></div>
            <table className="tbl"><tbody>
              {(s?.rows || []).map((r: any) => (
                <tr key={r.id}><td className="muted small" style={{ width: 70 }}>{fmtTime(r.ts).slice(-5)}</td>
                  <td style={{ width: 170 }}><b>{r.promoter_name}</b><div className="muted small ellipsis">{r.store}</div></td>
                  <td style={{ width: 150 }}><span className="pill good">{r.model} ×{r.units}</span></td>
                  <td className="small muted">“{r.raw}”</td>
                  <td style={{ width: 60 }}><span className="tag">{r.source === "llm" ? "模型" : "规则"}</span></td></tr>
              ))}
              {!s?.rows?.length && <tr><td className="muted">暂无。可以用右侧的模拟器以促销员身份发一条试试。</td></tr>}
            </tbody></table>
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <div className="card-head"><h2><BellRing size={15} /> 未报名单</h2>
              {canRemind && !!s?.missing?.length && <button className="btn sm primary" onClick={async () => { setRemind(await post("/field-reports/remind", { country, region: region || undefined })); reload(); }}>一键提醒</button>}</div>
            {!s?.missing?.length ? <div className="muted">全部已报 ✓</div> : s.missing.map((m: any) => (
              <div key={m.promoter_id} className="row between small" style={{ padding: "4px 0" }}><span><b>{m.name}</b> · {m.store}</span><span className="muted">{m.region}</span></div>
            ))}
            {remind && <div className="small" style={{ marginTop: 8 }}>
              {remind.sent.length > 0 && <div style={{ color: "var(--good)" }}>已提醒：{remind.sent.join("、")}</div>}
              {remind.failed.map((f: any) => <div key={f.name} className="muted">未发出：{f.name}（{f.reason}；需用 Meta 审核过的模板消息）</div>)}
            </div>}
          </div>
          <Simulator promoters={promoters} country={country} onSent={reload} />
        </div>
      </div>
    </>
  );
}

function Simulator({ promoters, country, onSent }: any) {
  const [phone, setPhone] = useState("");
  const [text, setText] = useState("");
  const [res, setRes] = useState<any>(null);
  const cur = phone || promoters[0]?.phones?.[0] || "";
  const send = async () => {
    if (!text.trim() || !cur) return;
    setRes(await post("/wa/simulate", { phone: cur, text })); setText(""); onSent();
  };
  return (
    <div className="card">
      <div className="card-head"><h2><MessageCircle size={15} /> 模拟促销员报数</h2><span className="muted small">与真实 WhatsApp 同一流程</span></div>
      <select value={cur} onChange={(e) => setPhone(e.target.value)} style={{ width: "100%" }}>
        {promoters.map((p: any) => <option key={p.promoter_id} value={p.phones[0]}>{p.name} · {p.store}</option>)}</select>
      <div className="row" style={{ margin: "8px 0", gap: 4 }}>{(SAMPLES[country] || []).map((x) => <button key={x} className="btn sm" onClick={() => setText(x)}>{x}</button>)}</div>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input style={{ flex: 1 }} value={text} placeholder="例如：Spark x3, Pop 9 x2" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
        <button className="btn primary sm" disabled={!text.trim()} onClick={send}><Send size={13} />发送</button>
      </div>
      {res && <div className="small" style={{ marginTop: 8 }}>{res.parsed ? <span style={{ color: "var(--good)" }}>识别 {res.parsed} 项：{res.items.map((i: any) => `${i.model}×${i.units}`).join("、")} · 今日累计 {res.total_today} 台（{res.source === "llm" ? "模型识别" : "规则识别"}）</span>
        : <span className="muted">没识别出型号和台数，已回复报数格式提示。</span>}</div>}
    </div>
  );
}

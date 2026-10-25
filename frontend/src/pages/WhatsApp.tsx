import { useEffect, useRef, useState } from "react";
import { fmtTime, post, STATUS } from "../api";
import { usePoll } from "../hooks";

const LANG: Record<string, string> = { en: "English", pcm: "Pidgin", ha: "Hausa", yo: "Yoruba", ig: "Igbo" };
const LEVEL: Record<string, [string, string]> = { low: ["good", "低风险"], attention: ["warn", "需关注"], reject: ["bad", "预审退回"] };

export default function WhatsApp() {
  const status = usePoll<any>("/wa/status", 5000);
  const convs = usePoll<any[]>("/wa/conversations", 2000);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const current = sel || convs.data?.[0]?.conversation_id || null;

  const toggleKill = async () => {
    setErr(null);
    try { await post("/wa/killswitch", { on: !status.data?.killswitch }); status.reload(); }
    catch (e: any) { setErr(e.message); }
  };

  return (
    <>
      <header className="top">
        <h2>WhatsApp · 经销商助理</h2>
        <div className="row">
          <span className={`pill ${status.data?.live ? "good" : "warn"}`}>{status.data?.live ? "已接入 Meta Cloud API" : "模拟模式（未配置 Meta 凭证）"}</span>
          <button className={`btn ${status.data?.killswitch ? "primary" : "danger"}`} onClick={toggleKill}>
            {status.data?.killswitch ? "解除熔断" : "一键熔断对外发送"}</button>
        </div>
      </header>
      {err && <div className="err">{err}</div>}
      <Simulator dealers={status.data?.dealers || []} promoters={status.data?.promoters || []} onSent={(cid) => { convs.reload(); if (cid) setSel(cid); }} />
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,260px) minmax(0,1fr)", gap: 12 }}>
        <div className="card" style={{ padding: 8 }}>
          {!convs.data?.length && <div className="muted" style={{ padding: 8 }}>还没有会话。用上方模拟器或手机给测试号发消息。</div>}
          {convs.data?.map((c) => (
            <div key={c.conversation_id} onClick={() => setSel(c.conversation_id)}
              style={{ padding: 8, borderRadius: 6, cursor: "pointer", background: c.conversation_id === current ? "var(--accent-soft)" : undefined }}>
              <b>{c.dealer?.name || "未登记号码"}</b> <span className="muted">{c.dealer?.dealer_id}</span>
              <div className="muted">+{c.phone} · {LANG[c.lang] || "—"}{c.human_mode ? " · 人工处理中" : ""}</div>
              <div className="muted" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {c.last?.direction === "in" ? "← " : "→ "}{c.last?.text}</div>
            </div>
          ))}
        </div>
        {current ? <Conversation id={current} /> : <div className="card muted">选择一个会话</div>}
      </div>
    </>
  );
}

function Simulator({ dealers, promoters = [], onSent }: { dealers: any[]; promoters?: any[]; onSent: (cid?: string) => void }) {
  const [phone, setPhone] = useState("");
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!phone && dealers[0]) setPhone(dealers[0].phones[0]); }, [dealers, phone]);
  const send = async () => {
    setErr(null);
    try { const r = await post("/wa/simulate", { phone, text }); setText(""); onSent(r.conversation_id); }
    catch (e: any) { setErr(e.message); }
  };
  const samples = ["How much is the TECNO Spark X6851 and do I have stock?", "Abeg wetin be warranty for Infinix Hot 50?",
    "Sannu, nawa ne farashin itel A70?", "Can you give me 20% discount and deliver tomorrow?", "agent"];
  return (
    <div className="card">
      <div className="muted" style={{ marginBottom: 6 }}>经销商模拟器：以经销商身份发消息，处理流程与真实 Webhook 完全相同</div>
      <div className="row">
        <select value={phone} onChange={(e) => setPhone(e.target.value)}>
          <optgroup label="经销商 → 经销商助理">{dealers.map((d) => d.phones.map((p: string) => <option key={p} value={p}>{d.name}（{d.region}）+{p}</option>))}</optgroup>
          <optgroup label="促销员 → 一线日报员">{promoters.map((d: any) => <option key={d.phones[0]} value={d.phones[0]}>{d.name} · {d.store} +{d.phones[0]}</option>)}</optgroup>
          <option value="2348099999999">未登记号码 +2348099999999</option>
        </select>
        <input style={{ flex: 1, minWidth: 240 }} value={text} placeholder="输入经销商的消息" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && text && send()} />
        <button className="btn primary" disabled={!text} onClick={send}>发送</button>
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        {samples.map((s) => <button key={s} className="btn" style={{ fontSize: 12 }} onClick={() => setText(s)}>{s.slice(0, 28)}{s.length > 28 ? "…" : ""}</button>)}
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}

function Conversation({ id }: { id: string }) {
  const { data: c, reload } = usePoll<any>(`/wa/conversations/${id}`, 1500);
  const bottom = useRef<HTMLDivElement>(null);
  const [human, setHuman] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [c?.messages?.length, c?.pending?.length]);
  if (!c) return <div className="card muted">加载中…</div>;

  const run = async (fn: () => Promise<any>) => {
    setErr(null);
    try { await fn(); reload(); } catch (e: any) { setErr(e.message); }
  };
  const task = c.tasks?.[0];

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><b>{c.dealer?.name || "未登记号码"}</b> <span className="muted">{c.dealer?.dealer_id} · {c.dealer?.region} · +{c.phone}</span></div>
        <div className="row">
          <span className="pill">{LANG[c.lang] || "—"}{c.lang_source === "user" ? "（用户选择）" : ""}</span>
          <span className={`pill ${c.window_open ? "good" : "warn"}`}>{c.window_open ? "24h 窗口打开" : "窗口已关闭"}</span>
          {c.human_mode ? <button className="btn" onClick={() => run(() => post(`/wa/conversations/${id}/release`))}>交还 Bot</button> : null}
        </div>
      </div>
      {task && (
        <div className="muted" style={{ margin: "6px 0" }}>
          最近任务：{STATUS[task.status] || task.status} · {task.progress_done}/{task.progress_total} 步 · {task.notes?.[task.notes.length - 1]?.text}
        </div>
      )}
      <div style={{ background: "var(--bg)", borderRadius: 8, padding: 12, maxHeight: 520, overflow: "auto" }}>
        {c.messages.map((m: any) => (
          <div key={m.id} style={{ display: "flex", justifyContent: m.direction === "in" ? "flex-start" : "flex-end", margin: "6px 0" }}>
            <div style={{ maxWidth: "75%", padding: "8px 10px", borderRadius: 10, whiteSpace: "pre-wrap", fontSize: 14,
              background: m.direction === "in" ? "var(--panel)" : "#d9fdd3", color: m.direction === "in" ? "var(--ink)" : "#111" }}>
              {m.text}
              <div style={{ fontSize: 11, opacity: 0.6, marginTop: 4 }}>
                {fmtTime(m.created_at)} · {m.direction === "in" ? "经销商" : m.kind === "fixed" ? "固定话术" : m.kind === "human" ? "人工" : "Bot（已审批）"} · {m.status}
                {m.flags?.possible_injection ? " · ⚠ 疑似注入" : ""}
              </div>
            </div>
          </div>
        ))}
        {c.pending.map((a: any) => <Draft key={a.approval_id} a={a} onDone={reload} />)}
        <div ref={bottom} />
      </div>
      {c.human_mode ? (
        <div className="row" style={{ marginTop: 8 }}>
          <input style={{ flex: 1 }} placeholder="人工回复（渠道经理）" value={human} onChange={(e) => setHuman(e.target.value)} />
          <button className="btn primary" disabled={!human}
            onClick={() => run(async () => { await post(`/wa/conversations/${id}/reply`, { text: human }); setHuman(""); })}>发送</button>
        </div>
      ) : null}
      {err && <div className="err">{err}</div>}
    </div>
  );
}

function Draft({ a, onDone }: { a: any; onDone: () => void }) {
  const [text, setText] = useState(a.payload.args.text);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const lv = LEVEL[a.review?.level] || ["", ""];
  const decide = async (decision: string) => {
    setErr(null);
    try {
      const body: any = { decision, note: note || undefined };
      if (decision === "edit") body.edited_args = { ...Object.fromEntries(Object.entries(a.payload.args).filter(([k]) => !k.startsWith("_"))), text };
      await post(`/approvals/${a.approval_id}/decide`, body);
      onDone();
    } catch (e: any) { setErr(e.message); }
  };
  const edited = text !== a.payload.args.text;
  return (
    <div style={{ display: "flex", justifyContent: "flex-end", margin: "6px 0" }}>
      <div style={{ width: "75%", padding: 10, borderRadius: 10, border: "2px dashed var(--accent)", background: "var(--panel)" }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <b style={{ fontSize: 13 }}>待渠道经理审批的草稿</b>
          <span className="row"><span className={`pill ${lv[0]}`}>{lv[1]}</span><span className="pill">{STATUS[a.state]}</span></span>
        </div>
        {a.review?.issues?.map((i: any, k: number) => <div key={k} className="err">{i.msg}</div>)}
        {a.state === "returned" ? <pre>{a.payload.args.text}</pre> : (
          <textarea style={{ marginTop: 6, minHeight: 90 }} value={text} onChange={(e) => setText(e.target.value)} />
        )}
        <details><summary className="muted" style={{ fontSize: 12 }}>依据（{a.evidence?.length || 0}）</summary>
          {a.evidence?.map((e: any) => <pre key={e.ref}>{e.ref}{"\n"}{e.text.slice(0, 600)}</pre>)}</details>
        {a.state !== "returned" && (
          <>
            <input style={{ width: "100%", margin: "6px 0" }} placeholder="拒绝原因" value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="row">
              <button className="btn primary" onClick={() => decide(edited ? "edit" : "approve")}>{edited ? "修改后批准并发送" : "批准并发送"}</button>
              <button className="btn danger" disabled={!note} onClick={() => decide("reject")}>拒绝</button>
              <span className="muted" style={{ fontSize: 12 }}>过期 {fmtTime(a.expires_at)}</span>
            </div>
          </>
        )}
        {err && <div className="err">{err}</div>}
      </div>
    </div>
  );
}

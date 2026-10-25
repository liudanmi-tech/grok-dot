// 目标分解脑图（类似 XMind）：KPI → 周里程碑 → 每个数字员工的周 KPI 指标与举措 → 具体任务
import { useMemo, useState } from "react";
import { Minus, Plus, Sparkles, X } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";

const COL = { root: 0, ms: 330, agent: 680, task: 1060 };
const W = { root: 280, ms: 300, agent: 330, task: 300 };
const H = { root: 150, ms: 118, agent: 112, task: 46 };
const GAP = 14;

const MS_LABEL: Record<string, [string, string]> = {
  done: ["已达成", "good"], missed: ["未达成", "bad"], current: ["进行中", "info"], behind: ["落后", "bad"], upcoming: ["未开始", ""],
};
const COLOR: Record<string, string> = { good: "#30d158", bad: "#ff453a", info: "#0a84ff", warn: "#ff9f0a", "": "#aeaeb2" };
const DONE = ["delivered", "accepted"];
const taskTone = (s: string) => (DONE.includes(s) ? "good" : ["failed", "cancelled"].includes(s) ? "bad" : ["needs_human", "awaiting_approval"].includes(s) ? "warn" : "info");

type N = { id: string; kind: "root" | "ms" | "agent" | "task"; data: any; children: N[]; tone: string };

export default function MindMap({ kpiId, me, go, onClose }: { kpiId: string; me: any; go: (p: string) => void; onClose: () => void }) {
  const { data, reload } = usePoll<any>(`/kpis/${kpiId}/tree`, 5000);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [zoom, setZoom] = useState(0.9);
  const [busy, setBusy] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const isOpen = (id: string, def: boolean) => (id in open ? open[id] : def);
  const toggle = (id: string, def: boolean) => setOpen((o) => ({ ...o, [id]: !(id in o ? o[id] : def) }));
  const canPlan = data && (me?.id === data.kpi.owner || me?.roles?.some((r: string) => ["country_manager", "agent_ops"].includes(r)));

  const { nodes, edges, height } = useMemo(() => {
    if (!data) return { nodes: [], edges: [], height: 0 };
    const root: N = { id: "root", kind: "root", data: data.kpi, tone: data.kpi.progress.rag === "red" ? "bad" : data.kpi.progress.rag === "amber" ? "warn" : data.kpi.progress.rag === "green" ? "good" : "info",
      children: data.milestones.map((m: any) => ({
        id: `ms${m.week_no}`, kind: "ms", data: m, tone: m.planned ? MS_LABEL[m.status][1] : "",
        children: !isOpen(`ms${m.week_no}`, m.week_no === data.current_week) ? [] : m.agents.map((a: any) => ({
          id: `ag${m.week_no}-${a.bot_id}`, kind: "agent", data: { ...a, week_no: m.week_no }, tone: a.total ? (a.done === a.total ? "good" : "info") : "",
          children: !isOpen(`ag${m.week_no}-${a.bot_id}`, false) ? [] : a.tasks.map((t: any) => ({ id: `t${t.task_id}`, kind: "task", data: t, tone: taskTone(t.status), children: [] })),
        })),
      })),
    };
    // 递归布局：子树高度 = max(自身, 子节点之和)，父节点垂直居中于子节点
    const out: any[] = [], lines: any[] = [];
    const size = (n: N): number => Math.max(H[n.kind], n.children.reduce((s, c) => s + size(c), 0) + Math.max(0, n.children.length - 1) * GAP);
    const place = (n: N, top: number) => {
      const h = size(n);
      const y = top + h / 2 - H[n.kind] / 2;
      out.push({ ...n, x: COL[n.kind], y });
      let t = top + (h - (n.children.reduce((s, c) => s + size(c), 0) + Math.max(0, n.children.length - 1) * GAP)) / 2;
      for (const c of n.children) {
        const ch = size(c);
        const cy = t + ch / 2;
        lines.push({ x1: COL[n.kind] + W[n.kind], y1: y + H[n.kind] / 2, x2: COL[c.kind], y2: cy, tone: c.tone, id: c.id });
        place(c, t);
        t += ch + GAP;
      }
    };
    place(root, 0);
    return { nodes: out, edges: lines, height: size(root) };
  }, [data, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const planWeek = async (n: number) => {
    setBusy(n); setErr(null);
    try { await post(`/kpis/${kpiId}/weeks/${n}/plan`); await reload(); setOpen((o) => ({ ...o, [`ms${n}`]: true })); }
    catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const expandAll = (v: boolean) => {
    const o: Record<string, boolean> = {};
    for (const m of data?.milestones || []) { o[`ms${m.week_no}`] = v; for (const a of m.agents) o[`ag${m.week_no}-${a.bot_id}`] = v; }
    setOpen(o);
  };

  return (
    <div className="mm-bg" onClick={onClose}>
      <div className="mm" onClick={(e) => e.stopPropagation()}>
        <div className="mm-head">
          <div>
            <div className="mm-title">目标分解 · {data?.kpi.title || "…"}</div>
            <div className="muted small">按周拆成里程碑，再分到每个数字员工的周 KPI 指标与具体任务 · 点节点展开 / 收起，点任务进入对话</div>
          </div>
          <div className="row">
            <span className="mm-legend">{Object.entries({ 已达成: "good", 进行中: "info", 落后: "bad", 待处理: "warn", 未开始: "" }).map(([l, t]) => (
              <span key={l}><i style={{ background: COLOR[t] }} />{l}</span>))}</span>
            <button className="btn sm" onClick={() => expandAll(true)}>全部展开</button>
            <button className="btn sm" onClick={() => expandAll(false)}>收起</button>
            <span className="seg mini"><button onClick={() => setZoom(Math.max(0.5, zoom - 0.1))}><Minus size={12} /></button>
              <button className="on">{Math.round(zoom * 100)}%</button><button onClick={() => setZoom(Math.min(1.4, zoom + 0.1))}><Plus size={12} /></button></span>
            <button className="btn ghost sm" onClick={onClose}><X size={16} /></button>
          </div>
        </div>
        {err && <div className="err" style={{ padding: "0 20px" }}>{err}</div>}
        <div className="mm-canvas">
          {!data ? <div className="muted" style={{ padding: 30 }}>加载中…</div> : (
            <div className="mm-board" style={{ width: (COL.task + W.task + 40) * zoom, height: (height + 40) * zoom }}>
              <div style={{ transform: `scale(${zoom})`, transformOrigin: "0 0", position: "relative", width: COL.task + W.task + 40, height: height + 40 }}>
                <svg className="mm-lines" width={COL.task + W.task + 40} height={height + 40}>
                  {edges.map((e) => {
                    const mx = (e.x1 + e.x2) / 2;
                    return <path key={e.id} d={`M${e.x1 + 20},${e.y1 + 20} C${mx + 20},${e.y1 + 20} ${mx + 20},${e.y2 + 20} ${e.x2 + 20},${e.y2 + 20}`}
                      stroke={COLOR[e.tone] || "#aeaeb2"} strokeOpacity={0.55} strokeWidth={2.2} fill="none" />;
                  })}
                </svg>
                {nodes.map((n) => (
                  <div key={n.id} className={`mm-node ${n.kind} tone-${n.tone || "none"}`} style={{ left: n.x + 20, top: n.y + 20, width: W[n.kind as keyof typeof W], height: H[n.kind as keyof typeof H] }}>
                    {n.kind === "root" && <RootNode k={n.data} />}
                    {n.kind === "ms" && <MsNode m={n.data} open={isOpen(n.id, n.data.week_no === data.current_week)} onToggle={() => toggle(n.id, n.data.week_no === data.current_week)}
                      canPlan={canPlan} busy={busy === n.data.week_no} onPlan={() => planWeek(n.data.week_no)} current={n.data.week_no === data.current_week} />}
                    {n.kind === "agent" && <AgentNode a={n.data} open={isOpen(n.id, false)} onToggle={() => toggle(n.id, false)} go={go} />}
                    {n.kind === "task" && <div className="mm-task" onClick={() => go(`chat/${n.data.chat_id}`)} title={n.data.goal}>
                      <i style={{ background: COLOR[n.tone] }} /><span className="ellipsis">{n.data.goal}</span><em>{n.data.day.slice(5)}</em></div>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RootNode({ k }: any) {
  const p = k.progress;
  const pct = Math.min(100, (100 * p.actual) / Math.max(1, p.target_total));
  return (
    <div className="mm-root">
      <div className="small" style={{ opacity: .8 }}>{k.market?.code === "ID" ? "🇮🇩" : "🇳🇬"} {k.market?.name} · {k.owner_name || "待认领"}</div>
      <div className="mm-root-title">{k.title}</div>
      <div className="mm-root-num"><b>{pct < 10 ? pct.toFixed(1) : Math.round(pct)}%</b><span>{p.actual.toLocaleString()} / {p.target_total.toLocaleString()} 台</span></div>
      <div className="mm-bar light"><i style={{ width: `${Math.max(1, pct)}%` }} /></div>
      <div className="small" style={{ opacity: .8 }}>{k.start} – {k.end} · 节奏 {p.pace == null ? "—" : Math.round(p.pace * 100) + "%"} · 第 {p.days_elapsed}/{p.days_total} 天</div>
    </div>
  );
}

function MsNode({ m, open, onToggle, canPlan, busy, onPlan, current }: any) {
  const [label, tone] = m.planned ? MS_LABEL[m.status] : ["待分解", ""];
  const pct = Math.min(100, (100 * m.actual_units) / Math.max(1, m.target_units));
  return (
    <div className="mm-card" onClick={m.planned ? onToggle : undefined} style={{ cursor: m.planned ? "pointer" : "default" }}>
      <div className="row between" style={{ flexWrap: "nowrap" }}>
        <b>第 {m.week_no} 周{current ? " · 本周" : ""}</b>
        <span className={`pill ${tone}`}>{label}</span>
      </div>
      <div className="muted small">{m.start.slice(5)} – {m.end.slice(5)} · 目标 {m.target_units.toLocaleString()} 台{m.plan_status ? ` · ${m.plan_status === "confirmed" ? "计划已确认" : "计划草稿"}` : ""}</div>
      {m.planned ? <>
        <div className="row between small"><span>实际 <b>{m.actual_units.toLocaleString()}</b> 台</span><span className="muted">{m.agents.length} 名员工 {open ? "▾" : "▸"}</span></div>
        <div className="mm-bar"><i style={{ width: `${Math.max(1, pct)}%`, background: COLOR[tone] || "#0a84ff" }} /></div>
      </> : canPlan ? (
        <button className="btn primary sm" disabled={busy} onClick={(e) => { e.stopPropagation(); onPlan(); }}><Sparkles size={12} />{busy ? "总调度分解中…" : "生成本周分解"}</button>
      ) : <div className="muted small">到该周时由总调度自动分解</div>}
    </div>
  );
}

function AgentNode({ a, open, onToggle, go }: any) {
  const pct = a.total ? (100 * a.done) / a.total : 0;
  return (
    <div className="mm-card agent" onClick={a.total ? onToggle : undefined} style={{ borderLeftColor: a.color, cursor: a.total ? "pointer" : "default" }}>
      <div className="row between" style={{ flexWrap: "nowrap" }}>
        <span className="row" style={{ flexWrap: "nowrap", minWidth: 0 }}><i className="mm-dot" style={{ background: a.color }} />
          <b className="ellipsis" onClick={(e) => { e.stopPropagation(); go(`bot/${a.bot_id}`); }}>{a.name}</b></span>
        <span className="mm-kpi" title={a.kpi.source === "default" ? "岗位默认指标" : "总调度分解的指标"}>{a.kpi.name} · {a.kpi.target}{a.kpi.unit}</span>
      </div>
      <div className="small ellipsis2" title={a.objective}>{a.objective}</div>
      <div className="row between small muted"><span>任务 {a.done}/{a.total} 完成</span>{a.total > 0 && <span>{open ? "收起 ▾" : "展开 ▸"}</span>}</div>
      <div className="mm-bar"><i style={{ width: `${a.total ? Math.max(4, pct) : 0}%`, background: a.color }} /></div>
    </div>
  );
}

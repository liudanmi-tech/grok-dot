import { useEffect, useState } from "react";
import { Flag, Plus, Target, X } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { ConvRow } from "./Chats";

export const RAG: Record<string, [string, string]> = { green: ["good", "按节奏"], amber: ["warn", "略落后"], red: ["bad", "落后"] };
const BRANDS = ["TECNO", "Infinix", "itel"];
const REGIONS = ["Lagos", "North", "South-West", "South-East", "FCT"];

export function RagPill({ rag }: { rag?: string | null }) {
  if (!rag) return <span className="pill">数据未出</span>;
  const [cls, text] = RAG[rag];
  return <span className={`pill ${cls}`}>{text}</span>;
}

export function KpiBar({ p }: { p: any }) {
  const pct = (v: number) => `${Math.min(100, (100 * v) / Math.max(1, p.target_total))}%`;
  return (
    <div className="kpibar" title={`实际 ${p.actual} / 应完成 ${p.expected} / 总目标 ${p.target_total}`}>
      <i className={`fill ${p.rag || ""}`} style={{ width: pct(p.actual) }} />
      {p.expected > 0 && <b className="mark" style={{ left: pct(p.expected) }} />}
    </div>
  );
}

export function KpiNumbers({ p }: { p: any }) {
  return (
    <div className="row muted small" style={{ gap: 14 }}>
      <span>实际 <b className="ink">{p.actual.toLocaleString()}</b> / 应完成 {p.expected.toLocaleString()} / 目标 {p.target_total.toLocaleString()} 台</span>
      <span>节奏 <b className="ink">{p.pace == null ? "—" : `${Math.round(p.pace * 100)}%`}</b></span>
      <span>较基线 <b className="ink">{p.lift_pct == null ? "—" : `${p.lift_pct > 0 ? "+" : ""}${p.lift_pct}%`}</b></span>
      <span>第 {p.days_elapsed}/{p.days_total} 天</span>
    </div>
  );
}

export default function Cockpit({ me, go, onChange, switchUser, autoNew, team }: any) {
  const { data, reload } = usePoll<any[]>("/kpis", 4000);
  const today = usePoll<any>("/cockpit/today", 4000);
  const byId = Object.fromEntries((team || []).map((b: any) => [b.id, b]));
  const users = usePoll<any[]>("/users", 0);
  const [open, setOpen] = useState(!!autoNew);
  useEffect(() => { if (autoNew) setOpen(true); }, [autoNew]);
  const canCreate = me?.roles?.some((r: string) => ["country_manager", "supervisor"].includes(r));
  const all = (data || []).flatMap((k: any) => [k, ...(k.children || [])]);
  const reports = all.filter((k: any) => k.last_report).map((k: any) => ({ ...k.last_report, kpi_id: k.kpi_id }));
  const red = all.filter((k: any) => k.progress.rag === "red").length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>目标驾驶舱</h1>
          <p>领导设 KPI → 主管认领并组建 bot 小队 → 每天晨会派活、晚会复盘 → 日报自动上报。进度全部来自销量数据，不用逐个问人。</p>
        </div>
        {canCreate ? <button className="btn primary" onClick={() => setOpen(true)}><Plus size={14} />设定 KPI</button>
          : <button className="btn primary" onClick={() => { go("cockpit/new"); switchUser("u-country-manager"); }}><Plus size={14} />以国家经理身份设定 KPI</button>}
      </div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="card kpi"><div className="l"><Target size={14} />进行中的 KPI</div><div className="v">{all.filter((k: any) => k.status === "active").length}<span className="muted"> / {all.length}</span></div></div>
        <div className="card kpi"><div className="l"><Flag size={14} />落后</div><div className="v" style={{ color: red ? "var(--bad)" : undefined }}>{red}</div></div>
        <div className="card kpi"><div className="l">待认领</div><div className="v">{all.filter((k: any) => k.status !== "active").length}</div></div>
        <div className="card kpi"><div className="l">今日日报</div><div className="v">{all.filter((k: any) => k.today?.review === "done").length}<span className="muted"> / {all.filter((k: any) => k.status === "active").length}</span></div></div>
      </div>

      <div className="split">
        <div className="stack">
          <div className="card-head" style={{ marginTop: 4 }}><h2>目标</h2></div>
          {!all.length && <div className="card muted">还没有 KPI。{canCreate ? "点右上角「设定 KPI」开始。" : `你当前是 ${me?.name || "—"}，没有下达 KPI 的权限；点右上角切换为国家经理。`}</div>}
          {(data || []).map((k: any) => (
            <div key={k.kpi_id} className="card">
              <KpiCard k={k} go={go} />
              {k.children?.length > 0 && (
                <div className="kpi-children">
                  {k.children.map((c: any) => <div key={c.kpi_id} className="kpi-child"><KpiCard k={c} go={go} /></div>)}
                </div>
              )}
            </div>
          ))}
          <div className="card">
            <div className="card-head">
              <h2>今日进展</h2>
              <span className="muted small">{today.data ? `${today.data.conversations.length} 个任务对话 · 完成 ${today.data.done} · 进行中 ${today.data.running} · 等你 ${today.data.attention} · ${today.data.deliverables} 份交付` : "…"}</span>
            </div>
            {!today.data?.conversations.length && <div className="muted">今天还没有任务对话。晨会派活或新建对话后会出现在这里。</div>}
            {today.data?.conversations.map((c: any) => <ConvRow key={c.task_id} c={c} byId={byId} go={go} />)}
            <div style={{ marginTop: 6 }}><a className="small" style={{ cursor: "pointer" }} onClick={() => go("chats")}>全部任务对话 →</a></div>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2>日报</h2><span className="muted small">晚会后自动上报</span></div>
          {!reports.length && <div className="muted">还没有日报。主管开完晚会后会出现在这里。</div>}
          {reports.map((r: any) => (
            <div key={r.kpi_id + r.day} className="inbox-item" style={{ cursor: "pointer" }} onClick={() => go(`kpi/${r.kpi_id}`)}>
              <div style={{ minWidth: 0 }}>
                <div><b>{r.kpi}</b> <span className="muted small">{r.day} · {r.owner}</span></div>
                <div className="small">完成 {r.done}/{r.total} 项 · KPI {r.progress.actual}/{r.progress.expected} 台 · 节奏 {r.progress.pace == null ? "—" : Math.round(r.progress.pace * 100) + "%"}</div>
                {r.tomorrow && <div className="muted small">明日：{r.tomorrow}</div>}
                {r.risks?.length > 0 && <div className="err small">风险：{r.risks.join("；")}</div>}
              </div>
            </div>
          ))}
        </div>
      </div>
      {open && <NewKpi users={users.data || []} parents={data || []} onClose={() => { setOpen(false); if (autoNew) go("cockpit"); }}
        onDone={(k: any) => { setOpen(false); reload(); onChange?.(); go(`kpi/${k.kpi_id}`); }} />}
    </>
  );
}

function KpiCard({ k, go }: any) {
  const p = k.progress;
  return (
    <div style={{ cursor: "pointer" }} onClick={() => go(`kpi/${k.kpi_id}`)}>
      <div className="row between">
        <div className="row"><b style={{ fontSize: 15 }}>{k.title}</b><RagPill rag={p.rag} />
          {k.status !== "active" && <span className="pill warn">待 {k.owner_name || "负责人"} 认领</span>}</div>
        <span className="muted small">负责人 {k.owner_name || "—"} · 小队 {k.squad.length} 名 bot</span>
      </div>
      <KpiBar p={p} />
      <div className="row between">
        <KpiNumbers p={p} />
        <span className="row small">
          <span className={`pill ${k.today?.standup ? "good" : ""}`}>晨会{k.today?.standup ? "✓" : "—"}</span>
          <span className={`pill ${k.today?.review === "done" ? "good" : k.today?.review ? "warn" : ""}`}>晚会{k.today?.review === "done" ? "✓" : k.today?.review ? "待主持" : "—"}</span>
        </span>
      </div>
    </div>
  );
}

export function NewKpi({ users, parents, onClose, onDone }: any) {
  const leads = users.filter((u: any) => u.roles.includes("supervisor"));
  const [f, setF] = useState<any>({ title: "拉各斯 TECNO 终端销量提升 15%", brand: ["TECNO"], region: ["Lagos"], uplift_pct: 15, weeks: 4,
    owner: leads[0]?.id || "", parent_id: "" });
  const [err, setErr] = useState<string | null>(null);
  const toggle = (k: string, v: string) => setF({ ...f, [k]: f[k].includes(v) ? f[k].filter((x: string) => x !== v) : [...f[k], v] });
  const submit = async () => {
    setErr(null);
    try {
      onDone(await post("/kpis", { ...f, brand: f.brand.length ? f.brand : null, region: f.region.length ? f.region : null,
        owner: f.owner || null, parent_id: f.parent_id || null, uplift_pct: Number(f.uplift_pct), weeks: Number(f.weeks) }));
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between"><b>设定 KPI</b><button className="btn ghost sm" onClick={onClose}><X size={14} /></button></div>
        <div className="field"><label>名称</label><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div className="field"><label>品牌（不选 = 全部）</label><div className="row">{BRANDS.map((b) => <span key={b} className={`mention ${f.brand.includes(b) ? "on" : ""}`} onClick={() => toggle("brand", b)}>{b}</span>)}</div></div>
        <div className="field"><label>区域（不选 = 全国）</label><div className="row">{REGIONS.map((r) => <span key={r} className={`mention ${f.region.includes(r) ? "on" : ""}`} onClick={() => toggle("region", r)}>{r}</span>)}</div></div>
        <div className="row">
          <div className="field" style={{ flex: 1 }}><label>终端销量提升 %（对比开始前 14 天日均）</label><input type="number" value={f.uplift_pct} onChange={(e) => setF({ ...f, uplift_pct: e.target.value })} /></div>
          <div className="field" style={{ flex: 1 }}><label>周期（周）</label><input type="number" value={f.weeks} onChange={(e) => setF({ ...f, weeks: e.target.value })} /></div>
        </div>
        <div className="field"><label>负责主管</label>
          <select value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })}>
            <option value="">暂不指定（开放认领）</option>
            {leads.map((u: any) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select></div>
        <div className="field"><label>上级 KPI（可选，用于 KPI 树）</label>
          <select value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
            <option value="">无（顶层 KPI）</option>
            {parents.map((k: any) => <option key={k.kpi_id} value={k.kpi_id}>{k.title}</option>)}
          </select></div>
        {err && <div className="err">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}>
          <button className="btn" onClick={onClose}>取消</button><button className="btn primary" onClick={submit}>下达</button>
        </div>
      </div>
    </div>
  );
}


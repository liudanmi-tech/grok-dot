import { useMemo, useRef, useState } from "react";
import { Check, FilePlus2, Pencil, Search, ShieldCheck, Trash2, Upload, X } from "lucide-react";
import { api, fmtTime, post } from "../api";
import { usePoll } from "../hooks";
import { Markdown, MARKETS } from "../ui";

export const DOC_TYPES: Record<string, string> = {
  product: "产品卖点", price_band: "价格带", price_policy: "价格与促销政策", policy: "合作政策", channel: "渠道规范",
  aftersales: "售后", faq: "常见问答", other: "其他",
};
const TYPE_ALIAS: Record<string, string> = { dealer_price_list: "price_band", price_list: "price_band", warranty: "aftersales", ordering: "channel",
  process: "channel", spec: "product", script: "faq", note: "other" };
const typeLabel = (t?: string) => DOC_TYPES[t || ""] || DOC_TYPES[TYPE_ALIAS[t || ""]] || t || "其他";

// 知识库：内置文档（随版本发布）+ 上传文档（审定后才对数字员工可见）；按国家隔离；检索预览与员工执行时同一套规则
export default function Knowledge({ me }: any) {
  const { data, reload } = usePoll<any[]>("/kb", 0);
  const [country, setCountry] = useState("全部");
  const [type, setType] = useState("全部");
  const [status, setStatus] = useState("全部");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [edit, setEdit] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const canEdit = me?.roles?.some((r: string) => ["agent_ops", "country_manager", "supervisor", "brand_manager", "channel_manager"].includes(r));
  const canApprove = me?.roles?.some((r: string) => ["agent_ops", "country_manager", "brand_manager"].includes(r));
  const docs = data || [];
  const ql = q.trim().toLowerCase();
  const list = docs.filter((d) => (country === "全部" || d.country === country) && (type === "全部" || typeLabel(d.doc_type) === type)
    && (status === "全部" || (status === "待审定" ? !d.approved : status === "已审定" ? d.approved : d.source === "upload"))
    && (!ql || `${d.title} ${d.snippet}`.toLowerCase().includes(ql)));
  const pending = docs.filter((d) => d.source === "upload" && !d.approved).length;
  const types = useMemo(() => ["全部", ...Array.from(new Set(docs.map((d) => typeLabel(d.doc_type))))], [docs]);
  const run = async (fn: () => Promise<any>) => { setErr(null); try { await fn(); reload(); } catch (e: any) { setErr(e.message); } };
  const probe = async () => {
    if (!q.trim()) return;
    const c = country === "全部" ? "NG" : country;
    setPreview({ q, country: c, ...(await api(`/kb/search?q=${encodeURIComponent(q)}&country=${c}`)) });
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>知识库</h1>
          <p>共 {docs.length} 份文档 · 上传 {docs.filter((d) => d.source === "upload").length} · 待审定 <b style={{ color: pending ? "var(--warn)" : undefined }}>{pending}</b>。
            数字员工只检索已审定、且属于本市场的文档；对外岗位只看可见范围为「对外」的文档。</p>
        </div>
        {canEdit && <button className="btn primary" onClick={() => setEdit({ title: "", text: "", doc_type: "other", visibility: "internal", country: country === "全部" ? "NG" : country })}>
          <FilePlus2 size={14} />新建 / 上传文档</button>}
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="seg">{["全部", ...Object.keys(MARKETS)].map((c) => (
          <button key={c} className={country === c ? "on" : ""} onClick={() => setCountry(c)}>{c === "全部" ? "全部市场" : MARKETS[c].name}</button>))}</div>
        <select value={type} onChange={(e) => setType(e.target.value)}>{types.map((t) => <option key={t}>{t}</option>)}</select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>{["全部", "已审定", "待审定", "上传的"].map((t) => <option key={t}>{t}</option>)}</select>
        <span className="searchbox" style={{ flex: 1 }}><Search size={13} /><input value={q} placeholder="搜索标题或内容；回车 = 预览数字员工会检索到什么" onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") probe(); }} /></span>
        <button className="btn sm" disabled={!q.trim()} onClick={probe}>检索预览</button>
      </div>
      {err && <div className="err">{err}</div>}
      {preview && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="row between"><b className="small">「{preview.q}」· {MARKETS[preview.country].name}市场的数字员工会检索到 {preview.count} 份</b>
            <button className="btn ghost sm" onClick={() => setPreview(null)}><X size={13} /></button></div>
          {preview.results.map((r: any) => (
            <div key={r.doc_id} className="small row" style={{ flexWrap: "nowrap" }}>
              <span className="tag">相关度 {r.score}</span><a style={{ cursor: "pointer" }} onClick={() => setSel(r.doc_id)}>{r.title}</a>
              <span className="muted ellipsis">{r.text.slice(0, 80)}</span>
            </div>
          ))}
        </div>
      )}
      <div className="kb-split">
        <div className="card" style={{ padding: 4 }}>
          {list.map((d) => (
            <div key={d.doc_id} className={`kb-row ${sel === d.doc_id ? "on" : ""}`} onClick={() => setSel(d.doc_id)}>
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <b className="ellipsis">{d.title}</b>
                <span style={{ marginLeft: "auto" }} className={`pill ${d.approved ? "good" : "warn"}`}>{d.approved ? "已审定" : "待审定"}</span>
              </div>
              <div className="row small muted" style={{ gap: 8 }}>
                <span className="tag">{MARKETS[d.country]?.name || d.country}</span><span>{typeLabel(d.doc_type)}</span>
                <span>{d.visibility === "external" ? "对外" : "内部"}</span><span>v{d.version}</span>
                <span>{d.source === "builtin" ? "内置" : `上传 · ${d.updated_by || d.created_by}`}</span>
              </div>
            </div>
          ))}
          {!list.length && <div className="muted" style={{ padding: 12 }}>没有匹配的文档。</div>}
        </div>
        <div className="card">
          {sel ? <DocView id={sel} canEdit={canEdit} canApprove={canApprove} run={run} onEdit={setEdit} onDeleted={() => setSel(null)} />
            : <div className="panel-empty">选择左侧文档查看内容。</div>}
        </div>
      </div>
      {edit && <DocEditor doc={edit} onClose={() => setEdit(null)} onSaved={(d: any) => { setEdit(null); reload(); setSel(d.doc_id); }} />}
    </>
  );
}

function DocView({ id, canEdit, canApprove, run, onEdit, onDeleted }: any) {
  const { data: d, reload } = usePoll<any>(`/kb/doc?id=${encodeURIComponent(id)}`, 0);
  if (!d) return <div className="muted">加载中…</div>;
  const up = d.source === "upload";
  return (
    <>
      <div className="row between" style={{ alignItems: "flex-start" }}>
        <div>
          <h2 style={{ margin: 0 }}>{d.title}</h2>
          <div className="muted small">{MARKETS[d.country]?.name} · {typeLabel(d.doc_type)} · {d.visibility === "external" ? "对外可见" : "仅内部"} · v{d.version} ·
            {up ? ` 上传：${d.created_by} · 更新 ${fmtTime(d.updated_at)}${d.approved_by ? ` · 审定：${d.approved_by}` : ""}` : " 内置文档（随版本发布，只读）"}</div>
        </div>
        <span className={`pill ${d.approved ? "good" : "warn"}`}>{d.approved ? "已审定 · 员工可检索" : "待审定 · 员工看不到"}</span>
      </div>
      {up && (
        <div className="row" style={{ margin: "8px 0" }}>
          {canApprove && !d.approved && <button className="btn primary sm" onClick={() => run(async () => { await post(`/kb/doc/approve?id=${encodeURIComponent(id)}`); reload(); })}><ShieldCheck size={13} />审定发布</button>}
          {canEdit && <button className="btn sm" onClick={() => onEdit(d)}><Pencil size={13} />编辑（会重新审定）</button>}
          {canEdit && <button className="btn sm danger" onClick={() => { if (confirm(`删除「${d.title}」？`)) run(async () => { await api(`/kb/doc?id=${encodeURIComponent(id)}`, { method: "DELETE" }); onDeleted(); }); }}><Trash2 size={13} />删除</button>}
        </div>
      )}
      <div className="output rich drawer-doc"><Markdown text={d.text} /></div>
    </>
  );
}

function DocEditor({ doc, onClose, onSaved }: any) {
  const [f, setF] = useState<any>({ ...doc });
  const [err, setErr] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const isNew = !doc.doc_id;
  const load = async (fl: File) => {
    if (!/\.(md|markdown|txt)$/i.test(fl.name)) { setErr("目前支持 .md / .txt（PDF、Word 解析在接入 RAGFlow 后支持）"); return; }
    const text = await fl.text();
    setF({ ...f, text, title: f.title || fl.name.replace(/\.(md|markdown|txt)$/i, "") });
  };
  const save = async () => {
    setErr(null);
    try {
      const body = { title: f.title, text: f.text, doc_type: f.doc_type, visibility: f.visibility, country: f.country, lang: f.lang || "zh" };
      onSaved(isNew ? await post("/kb", body) : await api(`/kb/doc?id=${encodeURIComponent(doc.doc_id)}`, { method: "PUT", body: JSON.stringify(body) }));
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="row between"><b>{isNew ? "新建 / 上传文档" : `编辑：${doc.title}`}</b><button className="btn ghost sm" onClick={onClose}><X size={14} /></button></div>
        <div className="field"><label>标题</label><input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
        <div className="row">
          <div className="field" style={{ flex: 1 }}><label>市场</label><select value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })}>
            {Object.entries(MARKETS).map(([c, m]) => <option key={c} value={c}>{m.name}</option>)}</select></div>
          <div className="field" style={{ flex: 1 }}><label>类型</label><select value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value })}>
            {Object.entries(DOC_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
          <div className="field" style={{ flex: 1 }}><label>可见范围</label><select value={f.visibility} onChange={(e) => setF({ ...f, visibility: e.target.value })}>
            <option value="internal">仅内部岗位</option><option value="external">对外岗位也可用（经销商助理等）</option></select></div>
        </div>
        <div className="field">
          <label className="row between"><span>正文（Markdown）</span>
            <span><input ref={file} type="file" accept=".md,.markdown,.txt" hidden onChange={(e) => e.target.files?.[0] && load(e.target.files[0])} />
              <button className="btn sm" onClick={() => file.current?.click()}><Upload size={13} />从文件导入</button></span></label>
          <textarea rows={14} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder="写入价格、政策、卖点等审定内容。数字员工只会引用审定后的文档。" />
        </div>
        <div className="muted small">保存后状态为「待审定」，由智能体运营、国家经理或品牌经理审定后才对数字员工可见。</div>
        {err && <div className="err">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}>
          <button className="btn" onClick={onClose}>取消</button><button className="btn primary" onClick={save}><Check size={14} />保存</button>
        </div>
      </div>
    </div>
  );
}

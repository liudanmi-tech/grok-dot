import { useMemo, useState } from "react";
import { BookOpen, Check, ExternalLink, Search, Sparkles, Workflow, X } from "lucide-react";
import { api, post } from "../api";
import { usePoll } from "../hooks";
import { Markdown } from "../ui";

// 技能广场：开源方法技能（装到岗位，执行时按目标注入）+ 平台内置流程技能（可执行步骤）
export default function SkillPlaza({ me }: any) {
  const { data, reload } = usePoll<any>("/skill-plaza", 0);
  const [tab, setTab] = useState<"rec" | "all" | "flow">("rec");
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("全部");
  const [open, setOpen] = useState<string | null>(null);
  const canInstall = me?.roles?.some((r: string) => ["agent_ops", "country_manager", "supervisor"].includes(r));
  const packs: any[] = data?.packs || [];
  const tplName = Object.fromEntries((data?.templates || []).map((t: any) => [t.id, t]));
  const cats = useMemo(() => ["全部", ...Array.from(new Set(packs.map((p) => p.category)))], [packs]);
  const ql = q.trim().toLowerCase();
  const match = (p: any) => !ql || `${p.id} ${p.zh || ""} ${p.description}`.toLowerCase().includes(ql);
  const shown = packs.filter((p) => (tab === "rec" ? p.zh : true) && (cat === "全部" || p.category === cat) && match(p))
    .sort((a, b) => (b.installed.length - a.installed.length) || (a.zh ? -1 : 1));
  const installedCount = packs.filter((p) => p.installed.length).length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>技能广场</h1>
          <p>共 {packs.length} 个开源方法技能（{installedCount} 个已装到岗位）+ {data?.workflows?.length || 0} 个内置流程技能。
            方法技能不执行代码：数字员工写交付时，按任务目标自动挑选最相关的 1–2 个作为方法论参考。</p>
        </div>
      </div>
      <div className="row between" style={{ marginBottom: 12 }}>
        <div className="seg">
          <button className={tab === "rec" ? "on" : ""} onClick={() => setTab("rec")}><Sparkles size={13} />推荐给本团队</button>
          <button className={tab === "all" ? "on" : ""} onClick={() => setTab("all")}><BookOpen size={13} />全部方法技能</button>
          <button className={tab === "flow" ? "on" : ""} onClick={() => setTab("flow")}><Workflow size={13} />内置流程技能</button>
        </div>
        {tab !== "flow" && (
          <div className="row">
            <select value={cat} onChange={(e) => setCat(e.target.value)}>{cats.map((c) => <option key={c}>{c}</option>)}</select>
            <span className="searchbox"><Search size={13} /><input value={q} placeholder="搜索技能…" onChange={(e) => setQ(e.target.value)} /></span>
          </div>
        )}
      </div>

      {tab === "flow" ? (
        <div className="grid g3">
          {(data?.workflows || []).map((w: any) => (
            <div key={w.name} className="card skill">
              <div className="row between"><b className="mono small">{w.name}</b><span className="pill info">流程 · v{w.version}</span></div>
              <div className="small">{w.description}</div>
              <ol className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{(w.step_titles || []).map((s: string, i: number) => <li key={i}>{s}</li>)}</ol>
              <div className="row">{(w.bots || []).map((b: string) => <span key={b} className="tag">{tplName[b]?.name || b}</span>)}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid g3">
          {shown.map((p) => (
            <div key={p.id} className="card skill" onClick={() => setOpen(p.id)}>
              <div className="row between" style={{ flexWrap: "nowrap" }}>
                <b className="ellipsis">{p.zh || p.name}</b>
                <span className="tag">{p.category}</span>
              </div>
              <div className="mono small muted">{p.id}{p.version ? ` · v${p.version}` : ""}</div>
              <div className="small ellipsis3">{p.zh_desc || p.description.replace(/^["']|["']$/g, "")}</div>
              <div className="row" style={{ gap: 4 }}>
                {p.installed.length ? p.installed.map((t: string) => (
                  <span key={t} className="mention on mini" style={{ borderColor: tplName[t]?.color }}>{tplName[t]?.name || t}</span>
                )) : <span className="muted small">未安装</span>}
              </div>
              <div className="muted small">{p.source.name} · {p.source.license}</div>
            </div>
          ))}
          {!shown.length && <div className="card muted">没有匹配的技能。</div>}
        </div>
      )}
      {open && <PackDrawer id={open} templates={data?.templates || []} canInstall={canInstall} onClose={() => setOpen(null)} onSaved={reload} />}
    </>
  );
}

function PackDrawer({ id, templates, canInstall, onClose, onSaved }: any) {
  const { data: p } = usePoll<any>(`/skill-plaza/pack?id=${encodeURIComponent(id)}`, 0);
  const [sel, setSel] = useState<string[] | null>(null);
  const [ref, setRef] = useState<{ name: string; text: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const chosen = sel ?? p?.installed ?? [];
  const save = async () => { await post("/skill-plaza/install", { id, templates: chosen }); setSaved(true); onSaved(); };
  const openRef = async (name: string) => setRef({ name, text: (await api(`/skill-plaza/reference?id=${encodeURIComponent(id)}&name=${encodeURIComponent(name)}`)).text });
  return (
    <div className="drawer-bg" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        {!p ? <div className="muted">加载中…</div> : (
          <>
            <div className="row between" style={{ flexWrap: "nowrap" }}>
              <div><h2 style={{ margin: 0 }}>{p.zh || p.name}</h2><div className="mono small muted">{p.id}{p.version ? ` · v${p.version}` : ""} · {p.category}</div></div>
              <button className="btn ghost sm" onClick={onClose}><X size={15} /></button>
            </div>
            {p.zh_desc && <div style={{ margin: "8px 0" }}>{p.zh_desc}</div>}
            <div className="small muted" style={{ margin: "8px 0" }}>
              来源 <a href={p.source.url} target="_blank" rel="noreferrer">{p.source.name} <ExternalLink size={11} /></a> · 许可证 {p.source.license}
            </div>
            <div className="card" style={{ padding: 12, marginBottom: 10 }}>
              <b className="small">安装到岗位</b>
              <div className="muted small">安装后，该岗位的所有成员在写交付时会按任务目标考虑这项方法。</div>
              <div className="row" style={{ marginTop: 6 }}>
                {templates.map((t: any) => (
                  <span key={t.id} className={`mention ${chosen.includes(t.id) ? "on" : ""}`} style={{ cursor: canInstall ? "pointer" : "default" }}
                    onClick={() => { if (!canInstall) return; setSaved(false); setSel(chosen.includes(t.id) ? chosen.filter((x: string) => x !== t.id) : [...chosen, t.id]); }}>
                    {chosen.includes(t.id) && <Check size={11} />}{t.name}
                  </span>
                ))}
              </div>
              {canInstall ? <div className="row" style={{ marginTop: 8 }}><button className="btn primary sm" onClick={save}>保存</button>{saved && <span className="small" style={{ color: "var(--good)" }}>已保存</span>}</div>
                : <div className="muted small" style={{ marginTop: 6 }}>只有智能体运营、国家经理或主管可以安装。</div>}
            </div>
            {p.references?.length > 0 && (
              <div className="row small" style={{ marginBottom: 8 }}>参考资料：{p.references.map((r: string) => <a key={r} style={{ cursor: "pointer" }} onClick={() => openRef(r)}>{r}</a>)}</div>
            )}
            <div className="output rich drawer-doc"><Markdown text={ref ? `### 参考：${ref.name}\n\n${ref.text}` : p.body} /></div>
            {ref && <button className="btn sm" style={{ marginTop: 6 }} onClick={() => setRef(null)}>返回技能正文</button>}
          </>
        )}
      </div>
    </div>
  );
}

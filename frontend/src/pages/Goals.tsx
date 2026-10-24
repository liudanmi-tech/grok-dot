import { lazy, Suspense, useEffect, useState } from "react";
import { Box, Globe2, LayoutGrid, Plus } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { setDemoMarket } from "../ui";
import { ConvRow } from "./Chats";
import { KpiBar, NewKpi, RagPill } from "./Cockpit";
import Kpi from "./Kpi";

const Office3D = lazy(() => import("./Office3D"));  // three.js 只在切到 3D 时加载

// 目标：驾驶舱总览 + 单个 KPI 详情合在一个页面；右上角切换 2D 看板 / 3D 办公室
export default function Goals({ id, me, team, go, onChange, switchUser, autoNew }: any) {
  const { data, reload } = usePoll<any[]>("/kpis", 4000);
  const today = usePoll<any>("/cockpit/today", 4000);
  const users = usePoll<any[]>("/users", 0);
  const [mode, setMode] = useState<"2d" | "3d">(() => { try { return (localStorage.getItem("goals.mode") as any) || "2d"; } catch { return "2d"; } });
  const [open, setOpen] = useState(!!autoNew);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { if (autoNew) setOpen(true); }, [autoNew]);
  useEffect(() => { try { localStorage.setItem("goals.mode", mode); } catch { /* 忽略 */ } }, [mode]);

  const all = (data || []).flatMap((k: any) => [k, ...(k.children || [])]);
  const selected = id && all.some((k: any) => k.kpi_id === id) ? id : all[0]?.kpi_id;
  const canCreate = me?.roles?.some((r: string) => ["country_manager", "supervisor"].includes(r));
  const byId = Object.fromEntries((team || []).map((b: any) => [b.id, b]));
  const active = all.filter((k: any) => k.status === "active");
  const red = all.filter((k: any) => k.progress.rag === "red").length;
  const convs = today.data?.conversations || [];

  // 选中的 KPI 属于哪个市场，演示时钟就切到哪个市场的当地时间
  const selKpi = all.find((k: any) => k.kpi_id === selected);
  useEffect(() => { if (selKpi?.market?.code) setDemoMarket(selKpi.market.code); }, [selKpi?.market?.code]);
  const hasID = all.some((k: any) => k.market?.code === "ID");
  const [seeding, setSeeding] = useState(false);
  const seed = async () => {
    setSeeding(true);
    try { const r = await post("/demo/seed/indonesia"); reload(); onChange?.(); switchUser("u-lead-jakarta"); go(`goals/${r.kpi_id}`); }
    catch (e: any) { alert(e.message); } finally { setSeeding(false); }
  };
  const create = () => { if (canCreate) setOpen(true); else { go("goals/new"); switchUser("u-country-manager"); } };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>目标</h1>
          <p>{all.length ? <>共 {all.length} 个 KPI · 进行中 {active.length} · 落后 <b style={{ color: red ? "var(--bad)" : undefined }}>{red}</b> ·
            今日 {today.data ? `${convs.length} 个任务对话，完成 ${today.data.done}，进行中 ${today.data.running}，等你 ${today.data.attention}` : "…"}</>
            : "领导设 KPI → 主管认领并组建 bot 小队 → 每天晨会派活、晚会复盘 → 日报自动上报。"}</p>
        </div>
        <div className="row">
          <div className="seg">
            <button className={mode === "2d" ? "on" : ""} onClick={() => setMode("2d")}><LayoutGrid size={13} />2D 看板</button>
            <button className={mode === "3d" ? "on" : ""} onClick={() => setMode("3d")}><Box size={13} />3D 办公室</button>
          </div>
          {!hasID && <button className="btn" disabled={seeding} onClick={seed} title="招募雅加达小队（含 4 个新岗位）、印尼国家经理下达 12.12 档期 KPI、雅加达主管认领开工">
            <Globe2 size={14} />{seeding ? "载入中…" : "载入印尼演示"}</button>}
          <button className="btn primary" onClick={create}><Plus size={14} />{canCreate ? "设定 KPI" : "以国家经理身份设定 KPI"}</button>
        </div>
      </div>

      {!all.length ? (
        <div className="card muted">还没有 KPI。{canCreate ? "点右上角「设定 KPI」开始。" : `你当前是 ${me?.name || "—"}，没有下达 KPI 的权限；点右上角切换为国家经理。`}</div>
      ) : (
        <>
          <div className="kpi-strip">
            {all.map((k: any) => (
              <div key={k.kpi_id} className={`kpi-tile ${k.kpi_id === selected ? "on" : ""} ${k.parent_id ? "child" : ""}`} onClick={() => go(`goals/${k.kpi_id}`)}>
                <div className="row" style={{ flexWrap: "nowrap" }}><span className="tag">{k.market?.name || "尼日利亚"}</span><b className="ellipsis">{k.title}</b>
                  <span style={{ marginLeft: "auto" }}><RagPill rag={k.progress.rag} /></span></div>
                <KpiBar p={k.progress} />
                <div className="row between small muted">
                  <span>{k.progress.actual.toLocaleString()} / {k.progress.target_total.toLocaleString()} 台 · 节奏 {k.progress.pace == null ? "—" : `${Math.round(k.progress.pace * 100)}%`}</span>
                  <span>{k.status === "active" ? k.owner_name : `待 ${k.owner_name || "负责人"} 认领`}</span>
                </div>
                <div className="row small" style={{ gap: 6 }}>
                  <span className={`pill ${k.today?.standup ? "good" : ""}`}>晨会{k.today?.standup ? "✓" : "—"}</span>
                  <span className={`pill ${k.today?.review === "done" ? "good" : k.today?.review ? "warn" : ""}`}>晚会{k.today?.review === "done" ? "✓" : k.today?.review ? "待主持" : "—"}</span>
                  <span className="muted">第 {k.progress.days_elapsed}/{k.progress.days_total} 天</span>
                </div>
              </div>
            ))}
          </div>

          {mode === "3d" ? (selected ? <Suspense fallback={<div className="card muted">加载 3D 办公室…</div>}><Office3D key={selected} kpiId={selected} go={go} /></Suspense>
            : <Office3DPlaceholder />) : (
            <>
              <div className="card" style={{ marginBottom: 14 }}>
                <div className="card-head">
                  <h2>今日进展</h2>
                  <span className="muted small">{today.data ? `${convs.length} 个任务对话 · ${today.data.deliverables} 份交付` : ""}</span>
                </div>
                {!convs.length && <div className="muted">今天还没有任务对话。晨会派活或新建对话后会出现在这里。</div>}
                {(showAll ? convs : convs.slice(0, 3)).map((c: any) => <ConvRow key={c.task_id} c={c} byId={byId} go={go} />)}
                {convs.length > 3 && <a className="small" style={{ cursor: "pointer" }} onClick={() => setShowAll(!showAll)}>{showAll ? "收起" : `展开全部 ${convs.length} 个 →`}</a>}
              </div>
              {selected && <Kpi key={selected} id={selected} me={me} team={team} go={go} onChange={() => { reload(); onChange?.(); }} switchUser={switchUser} />}
            </>
          )}
        </>
      )}
      {open && <NewKpi users={users.data || []} parents={data || []} onClose={() => { setOpen(false); if (autoNew) go("goals"); }}
        onDone={(k: any) => { setOpen(false); reload(); onChange?.(); go(`goals/${k.kpi_id}`); }} />}
    </>
  );
}

function Office3DPlaceholder() {
  return (
    <div className="card office3d">
      <div className="office3d-art" aria-hidden>
        {Array.from({ length: 6 }).map((_, i) => <span key={i} className="desk" style={{ animationDelay: `${i * 0.3}s` }} />)}
      </div>
      <h2>3D 办公室 · 下一版</h2>
      <p className="muted">卡通数字员工坐在工位上干活：干活中在打字、待命喝咖啡、下班离开工位、等你处理头顶冒感叹号；
        07:30 走进会议室开晨会，18:00 晚会围坐复盘；墙上 KPI 白板实时进度，大屏滚动团队群消息。点击角色进入他的工位与任务对话。</p>
      <p className="muted small">数据与 2D 看板完全一致，切回 2D 可看全部细节。</p>
    </div>
  );
}

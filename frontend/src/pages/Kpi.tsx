import { useEffect, useRef, useState } from "react";
import { ArrowUp, Moon, Sun, Users } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { Avatar, Markdown, TaskPill, timeAgo } from "../ui";
import { KpiBar, KpiNumbers, RagPill } from "./Cockpit";

const RATING: Record<string, [string, string]> = { good: ["good", "超出预期"], ok: ["", "符合预期"], behind: ["bad", "落后"] };

export default function Kpi({ id, me, team, go, onChange }: any) {
  const { data: k, error, reload } = usePoll<any>(`/kpis/${id}`, 2500);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { data: clock } = usePoll<any>("/demo/clock", 5000);
  if (error) return <div className="card err">{error}</div>;
  if (!k) return <div className="card muted">加载中…</div>;
  const byId = Object.fromEntries(team.map((b: any) => [b.id, b]));
  const owner = me?.id === k.owner;
  const run = async (fn: () => Promise<any>) => {
    setErr(null);
    try { await fn(); reload(); onChange?.(); } catch (e: any) { setErr(e.message); }
  };
  const standup = k.meetings.find((m: any) => m.kind === "standup");
  const review = k.meetings.find((m: any) => m.kind === "review");
  const todayStandup = standup && k.today?.standup ? standup : null;
  const todayReview = review && k.today?.review ? review : null;
  const week = k.weeks.find((w: any) => w.week_no === k.week_no);

  return (
    <>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row between">
          <div className="row"><h1 style={{ margin: 0, fontSize: 20 }}>{k.title}</h1><RagPill rag={k.progress.rag} /></div>
          <span className="muted small">下达 {k.created_by_name} · 负责人 <b>{k.owner_name || "待认领"}</b> · {k.start_date} ~ {k.end_date} · 第 {k.week_no} 周</span>
        </div>
        <KpiBar p={k.progress} />
        <KpiNumbers p={k.progress} />
        <Chart series={k.progress.series} />
        {err && <div className="err">{err}</div>}
      </div>

      {k.status !== "active" ? <Claim k={k} me={me} team={team} run={run} /> : (
        <div className="split">
          <div className="stack">
            <div className="card">
              <div className="card-head">
                <h2><Users size={15} /> 小队</h2>
                <span className="muted small">点成员看工位与工作流</span>
              </div>
              <div className="lanes">
                {k.squad_cards.map((b: any) => (
                  <div key={b.id} className="lane" onClick={() => go(`bot/${b.id}`)}>
                    <div className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={b} size="sm" /><b>{b.display_name}</b></div>
                    <div className="muted small lane-note">{b.current ? b.current.goal : b.activity === "needs_you" ? "等你处理" : "待命"}</div>
                    <div className="bar"><i style={{ width: `${b.current?.progress_total ? (100 * b.current.progress_done) / b.current.progress_total : b.current ? 5 : 0}%` }} /></div>
                  </div>
                ))}
              </div>
            </div>

            <div className="card">
              <div className="card-head">
                <h2><Sun size={15} /> 今日晨会</h2>
                {!todayStandup && (owner || me?.roles?.includes("country_manager")) && (
                  <button className="btn sm" disabled={busy} onClick={() => { setBusy(true); run(() => post(`/kpis/${id}/standup`)).finally(() => setBusy(false)); }}>
                    {busy ? "总调度正在开会…" : "现在开晨会"}</button>
                )}
              </div>
              {!todayStandup ? <div className="muted">{busy ? "总调度正在看 KPI、点评昨天、给每个 bot 派活（约半分钟）…" : "07:30 总调度自动主持晨会。也可以点右上角立即开。"}</div> : <Standup m={todayStandup} byId={byId} go={go} />}
            </div>

            <div className="card">
              <div className="card-head">
                <h2><Moon size={15} /> 今晚复盘</h2>
                {!todayReview && todayStandup && <button className="btn sm" onClick={() => run(() => post(`/kpis/${id}/review/prepare`))}>现在准备材料</button>}
              </div>
              {!todayReview ? <div className="muted">{clock?.phase === "review" && todayStandup ? "总调度正在整理每个 bot 今天的完成情况（约 1 分钟）…"
                : "18:00 系统备好每个 bot 的完成情况，由主管主持点评；提交后日报自动发给领导。"}</div>
                : <Review m={todayReview} byId={byId} owner={owner} run={run} id={id} />}
            </div>

            <div className="card">
              <div className="card-head"><h2>周分解</h2>
                {week && week.status !== "confirmed" && owner && <button className="btn sm primary" onClick={() => run(() => post(`/kpis/${id}/weeks/${week.week_no}/confirm`, {}))}>确认第 {week.week_no} 周</button>}
              </div>
              {k.weeks.map((w: any) => (
                <div key={w.week_no} style={{ marginBottom: 10 }}>
                  <div className="row"><b>第 {w.week_no} 周</b><span className="muted small">{w.start_date} ~ {w.end_date} · 需完成 {w.target_units} 台</span>
                    <span className={`pill ${w.status === "confirmed" ? "good" : "warn"}`}>{w.status === "confirmed" ? "已确认" : "草稿 · 照常执行"}</span></div>
                  {w.brief && <div className="muted small">{w.brief}</div>}
                  <table className="tbl"><tbody>
                    {w.items.map((i: any) => (
                      <tr key={i.bot_id}><td style={{ width: 150 }}><span className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={byId[i.bot_id]} size="sm" dot={false} />{byId[i.bot_id]?.display_name || i.bot_id}</span></td>
                        <td>{i.objective}</td><td className="muted small" style={{ width: 140 }}>{i.measure}</td></tr>
                    ))}
                  </tbody></table>
                </div>
              ))}
            </div>

            <History k={k} />
          </div>
          <Channel k={k} byId={byId} id={id} run={run} />
        </div>
      )}
    </>
  );
}

function Standup({ m, byId, go }: any) {
  const d = m.data;
  const res = Object.fromEntries((m.results || []).map((r: any) => [r.bot_id, r]));
  return (
    <>
      <div className="small" style={{ marginBottom: 8 }}>{d.summary}</div>
      {d.critiques.length > 0 && <h3>点评</h3>}
      {d.critiques.map((c: any) => (
        <div key={c.bot_id} className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start", margin: "4px 0" }}>
          <Avatar bot={byId[c.bot_id]} size="sm" dot={false} />
          <div className="small"><b>{byId[c.bot_id]?.display_name}</b> <span className={`pill ${RATING[c.rating][0]}`}>{RATING[c.rating][1]}</span> {c.comment}</div>
        </div>
      ))}
      <h3>今日任务</h3>
      <div className="lanes">
        {d.assignments.map((a: any) => {
          const r = res[a.bot_id];
          return (
            <div key={a.bot_id} className="lane" onClick={() => r && go(`bot/${a.bot_id}`)}>
              <div className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={byId[a.bot_id]} size="sm" dot={false} /><b>{byId[a.bot_id]?.display_name}</b>
                <span style={{ marginLeft: "auto" }}>{r && <TaskPill status={r.status} />}</span></div>
              <div className="small lane-goal">{a.goal}</div>
            </div>
          );
        })}
      </div>
      {m.mission?.output && (
        <details style={{ marginTop: 10 }}><summary className="small">总调度汇总</summary><div className="output rich"><Markdown text={m.mission.output} /></div></details>
      )}
    </>
  );
}

function Review({ m, byId, owner, run, id }: any) {
  const d = m.data;
  const [ratings, setRatings] = useState<Record<string, any>>({});
  const [adj, setAdj] = useState("");
  const [notes, setNotes] = useState("");
  if (m.status === "done") {
    const r = d.report;
    return (
      <>
        <div className="small">已提交 · 完成 {r.done}/{r.total} 项 · KPI {r.progress.actual}/{r.progress.expected} 台</div>
        {d.final.ratings.map((x: any) => (
          <div key={x.bot_id} className="small" style={{ margin: "4px 0" }}><b>{x.name}</b> <span className={`pill ${RATING[x.score][0]}`}>{RATING[x.score][1]}</span> {x.comment}</div>
        ))}
        {r.tomorrow && <div className="small"><b>明日：</b>{r.tomorrow}</div>}
        <div className="muted small">日报已发给领导。</div>
      </>
    );
  }
  return (
    <>
      <div className="small" style={{ marginBottom: 6 }}>{d.summary}</div>
      {d.rows.map((r: any) => {
        const g = ratings[r.bot_id] || {};
        const score = g.score || r.suggested;
        return (
          <div key={r.bot_id} className="review-row">
            <div className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={byId[r.bot_id]} size="sm" dot={false} /><b>{r.name}</b><TaskPill status={r.status} />
              <span className="muted small" style={{ marginLeft: "auto" }}>建议：{RATING[r.suggested][1]}</span></div>
            <div className="small muted">{r.goal}</div>
            <div className="small">{r.assessment}</div>
            {owner && (
              <div className="row" style={{ marginTop: 4 }}>
                {Object.entries(RATING).map(([key, [, label]]) => (
                  <span key={key} className={`mention ${score === key ? "on" : ""}`} onClick={() => setRatings({ ...ratings, [r.bot_id]: { ...g, score: key } })}>{label}</span>
                ))}
                <input style={{ flex: 1 }} placeholder="点评（会写入这个 bot 的记忆，明天起改进）" value={g.comment || ""}
                  onChange={(e) => setRatings({ ...ratings, [r.bot_id]: { ...g, score, comment: e.target.value } })} />
              </div>
            )}
          </div>
        );
      })}
      {d.risks?.length > 0 && <div className="err small">风险：{d.risks.join("；")}</div>}
      {owner ? (
        <>
          <div className="field"><label>明日调整（晨会会采纳）</label><input value={adj} placeholder={d.tomorrow?.join("；")} onChange={(e) => setAdj(e.target.value)} /></div>
          <div className="field"><label>备注</label><input value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
          <button className="btn primary" onClick={() => run(() => post(`/kpis/${id}/review`, { ratings, notes, adjustments: adj || (d.tomorrow || []).join("；") }))}>提交复盘并上报日报</button>
        </>
      ) : <div className="muted small">等负责人主持点评。</div>}
    </>
  );
}

function History({ k }: any) {
  const past = k.meetings.filter((m: any) => m.kind === "review" && m.status === "done");
  if (!past.length) return null;
  return (
    <div className="card">
      <h2>历史日报</h2>
      {past.map((m: any) => (
        <div key={m.meeting_id} className="small" style={{ padding: "4px 0", borderBottom: "1px solid var(--line-2)" }}>
          <b>{m.day}</b> 完成 {m.data.report.done}/{m.data.report.total} · KPI {m.data.report.progress.actual}/{m.data.report.progress.expected} 台
          {m.data.report.tomorrow && <span className="muted"> · 明日：{m.data.report.tomorrow}</span>}
        </div>
      ))}
    </div>
  );
}

function Channel({ k, byId, id, run }: any) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [k.channel.length]);
  return (
    <div className="card channel">
      <div className="card-head"><h2>团队群</h2><span className="muted small">晨会、进度、晚会自动广播</span></div>
      <div className="channel-feed">
        {k.channel.map((c: any) => (
          <div key={c.id} className={`ch ${c.kind}`}>
            {c.author_kind === "bot" ? <Avatar bot={byId[c.author] || { display_name: c.author_name }} size="sm" dot={false} />
              : <div className="av sm" style={{ background: "#334155" }}>{(c.author_name || "人").slice(0, 1)}</div>}
            <div style={{ minWidth: 0 }}>
              <div className="small"><b>{c.author_name}</b> <span className="muted">{timeAgo(c.ts)}</span></div>
              <div className="small" style={{ whiteSpace: "pre-wrap" }}>{c.text}</div>
            </div>
          </div>
        ))}
        <div ref={end} />
      </div>
      <div className="row" style={{ flexWrap: "nowrap", marginTop: 8 }}>
        <input style={{ flex: 1 }} value={text} placeholder="在群里说点什么…" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && text.trim()) run(async () => { await post(`/kpis/${id}/channel`, { text }); setText(""); }); }} />
        <button className="btn sm" disabled={!text.trim()} onClick={() => run(async () => { await post(`/kpis/${id}/channel`, { text }); setText(""); })}><ArrowUp size={13} /></button>
      </div>
    </div>
  );
}

function Claim({ k, me, team, run }: any) {
  const members = team.filter((b: any) => b.state === "active" && b.channel === "内部" && b.template !== "chief-of-staff");
  const [sel, setSel] = useState<string[]>(members.map((b: any) => b.id));
  const mine = !k.owner || me?.id === k.owner;
  return (
    <div className="card">
      <h2>认领 KPI 并组建 bot 小队</h2>
      <p className="muted">认领后，总调度会把 KPI 分解成每周目标，每天 07:30 主持晨会给小队派活。</p>
      <div className="row">
        {members.map((b: any) => (
          <span key={b.id} className={`mention ${sel.includes(b.id) ? "on" : ""}`}
            onClick={() => setSel(sel.includes(b.id) ? sel.filter((x) => x !== b.id) : [...sel, b.id])}>
            <Avatar bot={b} size="sm" dot={false} />{b.display_name}
          </span>
        ))}
      </div>
      <div style={{ marginTop: 10 }}>
        {mine ? <button className="btn primary" disabled={!sel.length} onClick={() => run(() => post(`/kpis/${k.kpi_id}/claim`, { squad: sel }))}>认领并开工</button>
          : <span className="muted">这个 KPI 指定给了 {k.owner_name}，请切换到该用户认领。</span>}
      </div>
    </div>
  );
}

function Chart({ series }: { series: any[] }) {
  const W = 720, H = 120, pad = 4;
  const vals = series.flatMap((s) => [s.actual ?? 0, s.target, s.baseline]);
  const max = Math.max(1, ...vals) * 1.1;
  const x = (i: number) => pad + (i * (W - 2 * pad)) / Math.max(1, series.length - 1);
  const y = (v: number) => H - pad - (v / max) * (H - 2 * pad);
  const bw = Math.max(2, (W - 2 * pad) / series.length - 3);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="kpi-chart" preserveAspectRatio="none">
      {series.map((s, i) => s.actual != null && (
        <rect key={i} x={x(i) - bw / 2} y={y(s.actual)} width={bw} height={H - pad - y(s.actual)} rx={2}
          className={s.actual >= s.target ? "bar-good" : "bar-bad"}><title>{s.date}：{s.actual} 台（目标 {s.target}）</title></rect>
      ))}
      <line x1={pad} x2={W - pad} y1={y(series[0]?.target || 0)} y2={y(series[0]?.target || 0)} className="line-target" />
      <line x1={pad} x2={W - pad} y1={y(series[0]?.baseline || 0)} y2={y(series[0]?.baseline || 0)} className="line-base" />
    </svg>
  );
}

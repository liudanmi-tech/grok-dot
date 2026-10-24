import { useEffect, useRef, useState } from "react";
import { ArrowUp, Moon, Sun, Users } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { ActivityPill, Avatar, DeliverableCard, Markdown, TaskPill, timeAgo } from "../ui";
import { KpiBar, KpiNumbers, RagPill } from "./Cockpit";

const RATING: Record<string, [string, string]> = { good: ["good", "超出预期"], ok: ["", "符合预期"], behind: ["bad", "落后"] };

export default function Kpi({ id, me, team, go, onChange, switchUser }: any) {
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

      {k.status !== "active" ? <Claim k={k} me={me} team={team} run={run} switchUser={switchUser} onHired={onChange} /> : (
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
                    <div className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={b} size="sm" /><b>{b.display_name}</b>
                      <span style={{ marginLeft: "auto" }}><ActivityPill a={b.activity} /></span></div>
                    <div className="muted small lane-note">{b.current ? b.current.goal
                      : b.activity === "needs_you" ? `有 ${b.attention + b.approvals} 件事等你处理`
                      : b.activity === "off_duty" ? `已下班 · 今日完成 ${b.done_today} 项`
                      : b.activity === "paused" ? "已暂停，不接新任务" : `待命中 · 今日完成 ${b.done_today} 项`}</div>
                    <div className="bar"><i style={{ width: `${b.current?.progress_total ? (100 * b.current.progress_done) / b.current.progress_total : b.current ? 5 : b.done_today ? 100 : 0}%`,
                      background: b.current ? undefined : "var(--line)" }} /></div>
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

            <Initiatives k={k} week={week} byId={byId} go={go} />

            <div className="card">
              <div className="card-head"><h2>周分解</h2>
                {week && week.status !== "confirmed" && owner && <button className="btn sm primary" onClick={() => run(() => post(`/kpis/${id}/weeks/${week.week_no}/confirm`, {}))}>确认第 {week.week_no} 周</button>}
              </div>
              {!k.weeks.length && <div className="muted">总调度正在把 KPI 分解成第一周目标（约半分钟）…</div>}
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

// 举措与产出：本周每个数字员工负责的举措 + 他们为这个 KPI 交出的最新产出（点开进入任务对话）
function Initiatives({ k, week, byId, go }: any) {
  const latest: Record<string, any> = {};
  const count: Record<string, number> = {};
  for (const m of [...k.meetings].reverse()) {
    if (m.kind !== "standup") continue;
    for (const r of m.results || []) {
      count[r.bot_id] = (count[r.bot_id] || 0) + (["delivered", "accepted"].includes(r.status) ? 1 : 0);
      latest[r.bot_id] = { ...r, mission: m.mission_task_id, day: m.day };
    }
  }
  if (!week) return null;
  return (
    <div className="card">
      <div className="card-head"><h2>举措与产出</h2><span className="muted small">第 {week.week_no} 周 · 本周需完成 {week.target_units} 台</span></div>
      {week.brief && <div className="small" style={{ marginBottom: 8 }}>{week.brief}</div>}
      <div className="initiatives">
        {week.items.map((i: any) => {
          const r = latest[i.bot_id];
          return (
            <div key={i.bot_id} className="initiative">
              <div className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={byId[i.bot_id]} size="sm" /><b>{byId[i.bot_id]?.display_name || i.bot_id}</b>
                <span className="muted small" style={{ marginLeft: "auto" }}>累计交付 {count[i.bot_id] || 0}</span></div>
              <div className="small"><b>举措：</b>{i.objective}</div>
              <div className="muted small">衡量：{i.measure}</div>
              {r ? (
                <div className="deliv-chip" onClick={() => go(`chat/${r.mission}`)}>
                  <div style={{ minWidth: 0 }}><div className="small"><b>{r.day} 产出</b> <TaskPill status={r.status} /></div>
                    <div className="small muted ellipsis2">{(r.output || r.goal || "").replace(/[#*|`>]/g, " ").slice(0, 140)}</div></div>
                  <span className="small" style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>对话 →</span>
                </div>
              ) : <div className="muted small">还没有产出，下次晨会派活。</div>}
            </div>
          );
        })}
      </div>
    </div>
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
      <div className="row between"><h3>今日任务</h3>{m.mission_task_id && <a className="small" style={{ cursor: "pointer" }} onClick={() => go(`chat/${m.mission_task_id}`)}>进入晨会任务对话 →</a>}</div>
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
        <details style={{ marginTop: 10 }}><summary className="small">总调度汇总</summary>{m.mission.has_card
          ? <DeliverableCard taskId={m.mission.task_id} index={0} fallback={m.mission.output} /> : <div className="output rich"><Markdown text={m.mission.output} /></div>}</details>
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

function Claim({ k, me, team, run, switchUser, onHired }: any) {
  const lib = usePoll<any[]>("/bot-templates", 0);
  const region = k.filter?.region?.[0] as string | undefined;
  const fits = (b: any) => !region || !b.scope?.region || b.scope.region.includes(region);
  const members = team.filter((b: any) => b.state === "active" && b.channel === "内部" && b.template !== "chief-of-staff");
  const [step, setStep] = useState(1);
  const [sel, setSel] = useState<string[] | null>(null);
  const [hiring, setHiring] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen: string[] = sel ?? members.filter(fits).map((b: any) => b.id);
  const mine = !k.owner || me?.id === k.owner;
  const p = k.progress;
  const hiredTpl = new Set(members.filter(fits).map((b: any) => b.template));
  const recruitable = (lib.data || []).filter((t: any) => t.channel === "内部" && t.id !== "chief-of-staff" && !hiredTpl.has(t.id));
  const hire = (t: any) => run(async () => {
    setHiring(t.id);
    try {
      const b = await post("/bots", { template: t.id, region });
      setSel([...chosen, b.id]); onHired?.(); lib.reload();
    } finally { setHiring(null); }
  });
  const toggle = (id: string) => setSel(chosen.includes(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);

  if (!mine) {
    return (
      <div className="card">
        <h2>等待 {k.owner_name} 接受这个 KPI</h2>
        <p className="muted">KPI 由 {k.created_by_name} 下达给 {k.owner_name}。负责人接受后组建 bot 小队，总调度再把目标分解到每周、每天。</p>
        <button className="btn primary" onClick={() => switchUser(k.owner)}>切换为 {k.owner_name}，去接受</button>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="wizard">
        {["接受 KPI", "组建 bot 小队", "分解并开工"].map((t, i) => (
          <div key={t} className={`wz ${step === i + 1 ? "on" : step > i + 1 ? "done" : ""}`}><span>{step > i + 1 ? "✓" : i + 1}</span>{t}</div>
        ))}
      </div>

      {step === 1 && (
        <>
          <h2>{k.created_by_name} 给你下达了一个 KPI</h2>
          <table className="tbl"><tbody>
            <tr><td style={{ width: 140 }} className="muted">目标</td><td><b>{k.title}</b></td></tr>
            <tr><td className="muted">范围</td><td>{(k.filter.brand || ["全部品牌"]).join("/")} · {(k.filter.region || ["全国"]).join("/")} · 终端销量</td></tr>
            <tr><td className="muted">基线 → 目标</td><td>日均 {Math.round(k.baseline_daily)} 台 → {p.target_daily} 台（+{k.uplift_pct}%），周期合计 {p.target_total.toLocaleString()} 台</td></tr>
            <tr><td className="muted">周期</td><td>{k.start_date} ~ {k.end_date}（{p.days_total} 天）</td></tr>
            <tr><td className="muted">考核方式</td><td>以销量数据为准，每天晨会派活、晚会复盘，日报自动上报给 {k.created_by_name}</td></tr>
          </tbody></table>
          <div className="row" style={{ marginTop: 12 }}><button className="btn primary" onClick={() => setStep(2)}>接受 KPI</button></div>
        </>
      )}

      {step === 2 && (
        <>
          <h2>组建 bot 小队</h2>
          <p className="muted">选择已有成员，或从智能体库为这个 KPI 招募新成员{region ? `（数据权限自动限定在 ${region}）` : ""}。</p>
          <h3>团队已有成员</h3>
          <div className="row">
            {members.map((b: any) => {
              const ok = fits(b);
              return (
                <span key={b.id} className={`mention ${chosen.includes(b.id) ? "on" : ""}`} style={ok ? undefined : { opacity: .45, cursor: "not-allowed" }}
                  title={ok ? "" : `负责 ${b.scope.region.join("/")}，数据范围与本 KPI 不重合`} onClick={() => ok && toggle(b.id)}>
                  <Avatar bot={b} size="sm" dot={false} />{b.display_name}
                </span>
              );
            })}
          </div>
          {recruitable.length > 0 && <h3>从智能体库招募</h3>}
          <div className="lanes">
            {recruitable.map((t: any) => (
              <div key={t.id} className="lane" style={{ cursor: "default" }}>
                <div className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={{ color: t.color, display_name: t.name }} size="sm" dot={false} /><b>{t.name}</b></div>
                <div className="small lane-goal">{t.summary}</div>
                <button className="btn sm" disabled={hiring === t.id} onClick={() => hire(t)}>{hiring === t.id ? "招募中…" : `招募${region ? ` · ${region}` : ""}`}</button>
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" onClick={() => setStep(1)}>上一步</button>
            <button className="btn primary" disabled={!chosen.length} onClick={() => setStep(3)}>下一步（已选 {chosen.length} 名）</button>
          </div>
        </>
      )}

      {step === 3 && (
        <>
          <h2>分解并开工</h2>
          <p className="muted">确认后，总调度把 KPI 分解成第一周每个 bot 的周目标（约半分钟），之后每天 07:30 晨会派活。周计划先按草稿执行，你随时可以确认或调整。</p>
          <div className="row">
            {chosen.map((id) => { const b = team.find((x: any) => x.id === id); return b && <span key={id} className="mention on"><Avatar bot={b} size="sm" dot={false} />{b.display_name}</span>; })}
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" onClick={() => setStep(2)}>上一步</button>
            <button className="btn primary" disabled={busy} onClick={() => { setBusy(true); run(() => post(`/kpis/${k.kpi_id}/claim`, { squad: chosen })).finally(() => setBusy(false)); }}>
              {busy ? "提交中…" : "确认小队，开始分解"}</button>
          </div>
        </>
      )}
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

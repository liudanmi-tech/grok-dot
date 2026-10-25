import { useState } from "react";
import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronRight, Gavel, Lightbulb, Microscope, Send, Target, TrendingUp, Users } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { Avatar } from "../ui";

const fmt = (n: number) => Math.round(n).toLocaleString();
const money = (sign: string, n: number) => (n === 0 ? "无需预算" : n >= 1e6 ? `${sign}${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)}M` : `${sign}${fmt(n)}`);
const TONE: Record<string, string> = { good: "#30d158", warn: "#ff9f0a", bad: "#ff453a" };
const PRI: Record<string, string> = { P0: "#ff453a", P1: "#ff9f0a", P2: "#0a84ff" };
const NAV: [string, string][] = [["gap", "差距拆解"], ["evidence", "根因与证据"], ["exec", "执行复盘"], ["plan", "改进建议"], ["forecast", "达成预测"], ["asks", "需要你拍板"]];

// KPI 改进建议落地页：结论 → 差距拆解 → 根因证据 → 执行复盘 → 建议 → 预测 → 需要拍板
export default function Improve({ id, go }: any) {
  const { data: r, error } = usePoll<any>(`/kpis/${id}/improve`, 0);
  if (error) return <div className="card err">{error}</div>;
  if (!r) return <div className="imp-loading"><div className="imp-spin" />正在汇总进度、数字员工交付与一线反馈…</div>;
  const s = r.summary;
  const jump = (k: string) => document.getElementById(`imp-${k}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <div className="imp">
      <button className="btn ghost sm" onClick={() => go(`goals/${id}`)}><ArrowLeft size={14} />返回目标</button>

      {/* 结论 */}
      <header className="imp-hero">
        <div className="imp-hero-glow" />
        <div className="imp-hero-top">
          <span className="imp-chip">{r.kpi.market?.name} · 第 {r.kpi.week_no} 周 · 第 {s.days_elapsed}/{s.days_total} 天</span>
          <span className="imp-chip">生成于 {r.generated_at}</span>
        </div>
        <div className="imp-hero-body">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="imp-eyebrow"><Lightbulb size={14} /> 改进建议</div>
            <h1>{r.kpi.title}</h1>
            <p className="imp-headline">{s.headline}</p>
            <div className="imp-hero-stats">
              <HeroStat label="到期差距" value={s.behind ? `-${fmt(s.gap)}` : "领先"} sub={`应完成 ${fmt(s.expected)} · 实际 ${fmt(s.actual)} 台`} tone={s.behind ? "bad" : "good"} />
              <HeroStat label="日均：现在 → 需要" value={`${fmt(s.cur_daily)} → ${fmt(s.need_daily)}`} sub={`剩 ${s.days_left} 天，每天还差 ${fmt(Math.max(0, s.need_daily - s.cur_daily))} 台`} tone="warn" />
              <HeroStat label="按现状到期" value={`${s.proj_now_pct}%`} sub={`${fmt(s.proj_now)} / ${fmt(s.target)} 台`} tone={s.proj_now_pct >= 100 ? "good" : "bad"} />
              <HeroStat label="全部采纳后" value={`${s.proj_plan_pct}%`} sub={`+${fmt(s.proj_plan - s.proj_now)} 台`} tone="good" />
            </div>
          </div>
          <Gauge v={s.health} />
        </div>
        <nav className="imp-nav">{NAV.map(([k, l], i) => <button key={k} onClick={() => jump(k)}><em>{i + 1}</em>{l}</button>)}</nav>
      </header>

      {/* 差距拆解 */}
      <Section id="gap" icon={<Target size={16} />} title="差距拆解" sub={s.behind ? `落后的 ${fmt(s.gap)} 台从哪里来` : "当前领先，以下为潜在风险敞口"}>
        <div className="imp-card">
          <div className="imp-stack">{r.drivers.map((d: any) => (
            <i key={d.key} style={{ flex: d.share, background: d.color }} title={`${d.label} ${Math.round(d.share * 100)}%`} onClick={() => jump("evidence")} />))}</div>
          <div className="imp-drivers">{r.drivers.map((d: any) => {
            const f = r.findings.find((x: any) => x.driver === d.key);
            return (
              <button key={d.key} className="imp-driver" onClick={() => document.getElementById(`imp-f-${f?.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
                <span className="dot" style={{ background: d.color }} />
                <div style={{ flex: 1, minWidth: 0 }}><b>{d.label}</b><div className="muted small ellipsis">{f?.title}</div></div>
                <div className="num"><b>{s.behind ? `-${fmt(d.units)}` : "—"}</b><span>{Math.round(d.share * 100)}%</span></div>
                <ChevronRight size={14} className="muted" />
              </button>);
          })}</div>
        </div>
      </Section>

      {/* 根因与证据 */}
      <Section id="evidence" icon={<Microscope size={16} />} title="根因与证据" sub="每条结论都附数据、图表和出处">
        <div className="imp-grid2">{r.findings.map((f: any) => <Finding key={f.id} f={f} driver={r.drivers.find((d: any) => d.key === f.driver)} go={go} />)}</div>
      </Section>

      {/* 执行复盘 */}
      <Section id="exec" icon={<Users size={16} />} title="执行复盘" sub="数字员工干得怎么样，人这边卡在哪里">
        <div className="imp-exec">
          <div className="imp-card" style={{ padding: 0, overflow: "hidden" }}>
            <div className="imp-card-head">数字员工<span className="muted small">交付 / 验收 / 返工 · 晚会点评</span></div>
            {r.agents.map((a: any) => (
              <div key={a.id} className="imp-agent" onClick={() => go(`bot/${a.id}`)}>
                <Avatar bot={a} size="sm" dot={false} />
                <div style={{ width: 150, flex: "none" }}><b className="ellipsis" style={{ display: "block" }}>{a.display_name}</b>
                  <span className="muted small">{a.level}{a.in_squad ? " · 小队" : " · 未入队"}</span></div>
                <div className="imp-agent-num"><b>{a.delivered}</b>/<b>{a.accepted}</b>/<b>{a.rework}</b>
                  <span className="muted small"> 👍{a.ratings.good} 👌{a.ratings.ok} 👎{a.ratings.behind}</span></div>
                <div className="imp-agent-note">
                  {a.strength && <div><CheckCircle2 size={12} color="#30d158" /> {a.strength}</div>}
                  {a.gap && <div><AlertTriangle size={12} color="#ff9f0a" /> {a.gap}</div>}
                </div>
              </div>))}
          </div>
          <div className="stack">
            <div className="imp-card">
              <div className="imp-card-head" style={{ padding: 0, border: 0, marginBottom: 10 }}>管理者（人）<span className="muted small">节奏与审批</span></div>
              <div className="imp-humans">{r.humans.map((h: any) => (
                <div key={h.label} className="imp-human" style={{ ["--c" as any]: TONE[h.tone] }}>
                  <span>{h.label}</span><b>{h.value}</b>{h.note && <em>{h.note}</em>}</div>))}</div>
            </div>
            <div className="imp-card imp-field" onClick={() => go("field")}>
              <div className="imp-card-head" style={{ padding: 0, border: 0, marginBottom: 8 }}>一线日报 · 今日<span className="muted small">实时</span></div>
              <div className="row" style={{ gap: 18 }}>
                <div><span className="muted small">已报 / 应报</span><b className="big">{r.field.reported ?? 0}<small>/{r.field.promoters ?? 0}</small></b></div>
                <div><span className="muted small">上报台数</span><b className="big">{r.field.units ?? 0}</b></div>
              </div>
              {!!r.field.missing?.length && <div className="small" style={{ color: "#c2410c", marginTop: 6 }}>未报：{r.field.missing.join("、")}</div>}
            </div>
          </div>
        </div>
      </Section>

      {/* 改进建议 */}
      <Section id="plan" icon={<Lightbulb size={16} />} title="改进建议" sub={`${r.suggestions.length} 条 · 按优先级排序 · 预计合计 +${fmt(s.proj_plan - s.proj_now)} 台`}>
        <ImpactBars list={r.suggestions} />
        <div className="stack" style={{ gap: 14 }}>{r.suggestions.map((x: any) => <Suggestion key={x.id} x={x} sign={r.sign} kpi={r.kpi} go={go} />)}</div>
      </Section>

      {/* 预测 */}
      <Section id="forecast" icon={<TrendingUp size={16} />} title="达成预测" sub="累计销量：实际、按现状外推、全部采纳（建议 3 天内爬坡到满效）">
        <div className="imp-card"><Forecast c={r.curve} s={s} /></div>
      </Section>

      {/* 需要拍板 */}
      <Section id="asks" icon={<Gavel size={16} />} title="需要你拍板" sub="以下事项超出数字员工与主管权限">
        <div className="imp-grid3">{r.asks.map((a: any, i: number) => <Ask key={i} a={a} sign={r.sign} />)}</div>
      </Section>

      <p className="imp-note">{r.note}</p>
    </div>
  );
}

function Section({ id, icon, title, sub, children }: any) {
  return (
    <section id={`imp-${id}`} className="imp-sec">
      <div className="imp-sec-head"><span className="imp-sec-ic">{icon}</span><h2>{title}</h2><span className="muted small">{sub}</span></div>
      {children}
    </section>
  );
}

function HeroStat({ label, value, sub, tone }: any) {
  return <div className="imp-hstat" style={{ ["--c" as any]: TONE[tone] }}><span>{label}</span><b>{value}</b><em>{sub}</em></div>;
}

function Gauge({ v }: { v: number }) {
  const R = 52, C = Math.PI * R, c = v >= 75 ? "#30d158" : v >= 55 ? "#ff9f0a" : "#ff453a";
  return (
    <div className="imp-gauge">
      <svg viewBox="0 0 140 84" width="168">
        <path d="M18 74 A52 52 0 0 1 122 74" fill="none" stroke="rgba(255,255,255,.14)" strokeWidth="12" strokeLinecap="round" />
        <path d="M18 74 A52 52 0 0 1 122 74" fill="none" stroke={c} strokeWidth="12" strokeLinecap="round"
          strokeDasharray={`${(C * v) / 100} ${C}`} className="imp-gauge-arc" />
      </svg>
      <b>{v}</b><span>目标健康度</span>
    </div>
  );
}

function Finding({ f, driver, go }: any) {
  return (
    <article id={`imp-f-${f.id}`} className="imp-card imp-finding" style={{ ["--c" as any]: driver?.color }}>
      <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
        <span className="imp-fid">{f.id}</span>
        <span className={`pill ${f.severity === "高" ? "bad" : "warn"}`}>影响 {f.severity}</span>
        <span className="pill">{driver?.label}</span>
        <span className="muted small" style={{ marginLeft: "auto" }}>可信度 {f.confidence}</span>
      </div>
      <h3>{f.title}</h3>
      <p>{f.summary}</p>
      <div className="imp-metrics">{f.metrics.map((m: any) => (
        <div key={m.label} style={{ ["--c" as any]: TONE[m.tone] }}><span>{m.label}</span><b>{m.value}</b></div>))}</div>
      <MiniChart ch={f.chart} />
      <div className="imp-src">
        {f.agent_bot && <button className="imp-by" onClick={() => go(`bot/${f.agent_bot.id}`)}><Avatar bot={f.agent_bot} size="sm" dot={false} />{f.agent_bot.display_name} 发现</button>}
        <span className="muted small">来源：{f.sources.join(" · ")}</span>
      </div>
    </article>
  );
}

function MiniChart({ ch }: any) {
  const [hover, setHover] = useState<number | null>(null);
  if (ch.type === "line") {
    const W = 460, H = 150, P = 24, all = ch.series.flatMap((x: any) => x.data), max = Math.max(...all) * 1.1, n = ch.labels.length;
    const X = (i: number) => P + (i * (W - P * 2)) / (n - 1), Y = (v: number) => H - 22 - (v / max) * (H - 40);
    return (
      <div className="imp-chart"><div className="imp-chart-t">{ch.title}
        <span className="imp-legend">{ch.series.map((x: any) => <span key={x.name}><i style={{ background: x.color }} />{x.name}</span>)}</span></div>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" onMouseLeave={() => setHover(null)}>
          {ch.mark && <g><line x1={X(ch.mark.at)} x2={X(ch.mark.at)} y1={10} y2={H - 22} stroke="#ff453a" strokeDasharray="3 3" opacity=".6" />
            <text x={X(ch.mark.at) + 4} y={18} fontSize="10" fill="#ff453a">{ch.mark.label}</text></g>}
          {ch.series.map((x: any) => (
            <g key={x.name}>
              <path d={`M${x.data.map((v: number, i: number) => `${X(i)},${Y(v)}`).join(" L")} L${X(n - 1)},${H - 22} L${X(0)},${H - 22}Z`} fill={x.color} opacity=".07" />
              <path className="imp-draw" d={`M${x.data.map((v: number, i: number) => `${X(i)},${Y(v)}`).join(" L")}`} fill="none" stroke={x.color} strokeWidth="2.4" strokeLinejoin="round" />
              {x.data.map((v: number, i: number) => <circle key={i} cx={X(i)} cy={Y(v)} r={hover === i ? 4.5 : 2.5} fill="#fff" stroke={x.color} strokeWidth="2" />)}
            </g>))}
          {ch.labels.map((l: string, i: number) => (
            <g key={l}><rect x={X(i) - 18} y={0} width={36} height={H} fill="transparent" onMouseEnter={() => setHover(i)} />
              {(i % 2 === 0 || i === n - 1) && <text x={X(i)} y={H - 6} fontSize="10" textAnchor="middle" fill="#86868b">{l}</text>}</g>))}
          {hover != null && <g><line x1={X(hover)} x2={X(hover)} y1={10} y2={H - 22} stroke="#c7c7cc" />
            <foreignObject x={Math.min(X(hover) + 6, W - 120)} y={20} width="116" height={20 + ch.series.length * 16}>
              <div className="imp-tip"><b>{ch.labels[hover]}</b>{ch.series.map((x: any) => <div key={x.name}><i style={{ background: x.color }} />{x.name} {x.data[hover]}</div>)}</div>
            </foreignObject></g>}
        </svg>
      </div>
    );
  }
  if (ch.type === "pie") {
    const total = ch.data.reduce((a: number, d: any) => a + d.value, 0), R = 46, C = 2 * Math.PI * R;
    let acc = 0;
    return (
      <div className="imp-chart"><div className="imp-chart-t">{ch.title}</div>
        <div className="row" style={{ gap: 18, flexWrap: "nowrap" }}>
          <svg viewBox="0 0 120 120" width="120">{ch.data.map((d: any, i: number) => {
            const fr = d.value / total, el = <circle key={i} className="imp-arc" cx="60" cy="60" r={R} fill="none" stroke={d.color} strokeWidth={hover === i ? 20 : 16}
              strokeDasharray={`${fr * C} ${C}`} strokeDashoffset={-acc * C} transform="rotate(-90 60 60)" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />;
            acc += fr; return el;
          })}<text x="60" y="58" textAnchor="middle" fontSize="18" fontWeight="800" fill="currentColor">{hover == null ? total : ch.data[hover].value}</text>
            <text x="60" y="74" textAnchor="middle" fontSize="9" fill="#86868b">{hover == null ? `合计 ${ch.unit}` : ch.data[hover].label}</text></svg>
          <div className="imp-pie-lg">{ch.data.map((d: any, i: number) => (
            <div key={d.label} className={hover === i ? "on" : ""} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <i style={{ background: d.color }} />{d.label}<b>{d.value}{ch.unit}</b><em>{Math.round((100 * d.value) / total)}%</em></div>))}</div>
        </div>
      </div>
    );
  }
  const max = Math.max(...ch.data.map((d: any) => d.value), ch.threshold || 0) * 1.08 || 1;
  return (
    <div className="imp-chart"><div className="imp-chart-t">{ch.title}{ch.threshold != null && <span className="muted small">虚线 = 安全线 {ch.threshold}{ch.unit}</span>}</div>
      <div className="imp-bars">{ch.data.map((d: any, i: number) => {
        const low = ch.threshold != null && (ch.title.includes("库存") ? d.value < ch.threshold : d.value < ch.threshold && i === 0);
        const mine = /TECNO|Infinix/.test(d.label);
        return (
          <div key={d.label} className="imp-bar">
            <span className="ellipsis">{d.label}</span>
            <span className="imp-bar-t">
              <i style={{ width: `${(100 * d.value) / max}%`, background: low || mine ? "#ff453a" : "#0a84ff", animationDelay: `${i * 70}ms` }} />
              {ch.threshold != null && <u style={{ left: `${(100 * ch.threshold) / max}%` }} />}
            </span>
            <b style={{ color: low || mine ? "#ff453a" : undefined }}>{d.value}{ch.unit}</b>
          </div>);
      })}</div>
    </div>
  );
}

function ImpactBars({ list }: any) {
  const max = Math.max(...list.map((x: any) => x.impact_units), 1);
  return (
    <div className="imp-card imp-impact">
      <div className="imp-chart-t">各条建议的预计增量（到期累计，台）</div>
      {list.map((x: any) => (
        <button key={x.id} className="imp-impact-row" onClick={() => document.getElementById(`imp-s-${x.id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
          <span className="imp-pri" style={{ background: PRI[x.priority] }}>{x.priority}</span>
          <span className="ellipsis" style={{ width: "38%" }}>{x.id} · {x.title}</span>
          <span className="imp-bar-t" style={{ flex: 1 }}><i style={{ width: `${(100 * x.impact_units) / max}%`, background: `linear-gradient(90deg, ${PRI[x.priority]}, ${PRI[x.priority]}aa)` }} /></span>
          <b>+{fmt(x.impact_units)}</b>
        </button>))}
    </div>
  );
}

function Suggestion({ x, sign, kpi, go }: any) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<Record<number, boolean>>({});
  const dispatch = async () => {
    setBusy(true); setErr(null);
    try {
      const goal = `落实改进建议「${x.title}」（目标：${kpi.title}）：${x.steps.join("；")}。负责人 ${x.owner}，截止 ${x.due}；衡量：${x.metric}`;
      const t = await post("/tasks", { bot_id: "chief-of-staff", goal, members: x.agent_bot ? [x.agent_bot.id] : undefined });
      go(`chat/${t.task_id}`);
    } catch (e: any) { setErr(e.message); setBusy(false); }
  };
  return (
    <article id={`imp-s-${x.id}`} className="imp-card imp-sug" style={{ ["--c" as any]: PRI[x.priority] }}>
      <div className="imp-sug-main">
        <div className="row" style={{ gap: 8 }}>
          <span className="imp-pri" style={{ background: PRI[x.priority] }}>{x.priority}</span>
          <span className="muted small">{x.id}</span>
          {x.finding && <button className="imp-link" onClick={() => document.getElementById(`imp-f-${x.finding}`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>依据 {x.finding} →</button>}
        </div>
        <h3>{x.title}</h3>
        <ol className="imp-steps">{x.steps.map((st: string, i: number) => (
          <li key={i} className={done[i] ? "done" : ""} onClick={() => setDone({ ...done, [i]: !done[i] })}><span>{done[i] ? "✓" : i + 1}</span>{st}</li>))}</ol>
        <div className="imp-kv">
          <div><span>负责人</span><b>{x.owner}</b></div>
          <div><span>截止</span><b>{x.due}</b></div>
          <div><span>投入</span><b>{money(sign, x.cost)}</b></div>
          <div><span>难度</span><b>{x.effort}</b></div>
          <div><span>把握</span><b>{x.confidence}</b></div>
        </div>
        <div className="imp-measure"><Target size={13} /> 衡量：{x.metric}</div>
        <div className="imp-risk"><AlertTriangle size={13} /> 风险：{x.risk}</div>
      </div>
      <aside className="imp-sug-side">
        <span className="muted small">预计增量</span>
        <b className="imp-big">+{fmt(x.impact_units)}<small> 台</small></b>
        <span className="muted small">满效后每天 +{fmt(x.impact_daily)} 台</span>
        {x.agent_bot && <div className="imp-agentchip"><Avatar bot={x.agent_bot} size="sm" dot={false} /><span>{x.agent_bot.display_name}<em>执行</em></span></div>}
        <button className="btn primary" disabled={busy} onClick={dispatch}><Send size={13} />{busy ? "派发中…" : "派发落实"}</button>
        {err && <div className="err small">{err}</div>}
      </aside>
    </article>
  );
}

function Forecast({ c, s }: any) {
  const [hover, setHover] = useState<number | null>(null);
  const n = c.labels.length, W = 900, H = 280, L = 54, R = 70, T = 16, B = 30;
  const e = s.days_elapsed;
  const max = Math.max(c.target[n - 1], ...c.plan, ...c.now) * 1.06;
  const X = (i: number) => L + (i * (W - L - R)) / (n - 1), Y = (v: number) => T + (1 - v / max) * (H - T - B);
  const line = (pts: [number, number][]) => pts.map(([i, v], j) => `${j ? "L" : "M"}${X(i)},${Y(v)}`).join(" ");
  const base: [number, number][] = e ? [[e - 1, s.actual]] : [];
  const nowPts = [...base, ...c.now.map((v: number, j: number) => [e + j, v] as [number, number])];
  const planPts = [...base, ...c.plan.map((v: number, j: number) => [e + j, v] as [number, number])];
  const val = (arr: [number, number][], i: number) => arr.find(([x]) => x === i)?.[1];
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round((max * f) / 100) * 100);
  return (
    <div>
      <div className="imp-legend" style={{ marginBottom: 6 }}>
        <span><i style={{ background: "#86868b" }} />目标</span><span><i style={{ background: "#0a84ff" }} />实际</span>
        <span><i style={{ background: "#ff453a" }} />按现状 {s.proj_now_pct}%</span><span><i style={{ background: "#30d158" }} />全部采纳 {s.proj_plan_pct}%</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" onMouseLeave={() => setHover(null)}>
        <defs><linearGradient id="impPlan" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#30d158" stopOpacity=".28" /><stop offset="1" stopColor="#30d158" stopOpacity="0" /></linearGradient></defs>
        {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={Y(t)} y2={Y(t)} stroke="rgba(0,0,0,.06)" /><text x={L - 8} y={Y(t) + 4} fontSize="10" textAnchor="end" fill="#86868b">{t.toLocaleString()}</text></g>)}
        {e > 0 && <rect x={X(e - 1)} y={T} width={X(n - 1) - X(e - 1)} height={H - T - B} fill="rgba(10,132,255,.035)" />}
        {planPts.length > 1 && <path d={`${line(planPts)} L${X(n - 1)},${Y(nowPts[nowPts.length - 1][1])} ${line([...nowPts].reverse()).replace(/^M/, "L")}Z`} fill="url(#impPlan)" />}
        <path d={line(c.target.map((v: number, i: number) => [i, v]))} fill="none" stroke="#86868b" strokeWidth="1.6" strokeDasharray="5 4" />
        <path className="imp-draw" d={line(nowPts)} fill="none" stroke="#ff453a" strokeWidth="2.2" strokeDasharray="6 4" />
        <path className="imp-draw" d={line(planPts)} fill="none" stroke="#30d158" strokeWidth="2.8" />
        {c.actual.length > 0 && <path d={line(c.actual.map((v: number, i: number) => [i, v]))} fill="none" stroke="#0a84ff" strokeWidth="3.2" strokeLinecap="round" />}
        {c.actual.map((v: number, i: number) => <circle key={i} cx={X(i)} cy={Y(v)} r="3.5" fill="#0a84ff" />)}
        <text x={X(n - 1) + 6} y={Y(c.target[n - 1]) + 4} fontSize="11" fill="#86868b">目标 {fmt(c.target[n - 1])}</text>
        <text x={X(n - 1) + 6} y={Y(c.plan[c.plan.length - 1] ?? 0) - 4} fontSize="11" fontWeight="700" fill="#248a3d">{s.proj_plan_pct}%</text>
        <text x={X(n - 1) + 6} y={Y(c.now[c.now.length - 1] ?? 0) + 12} fontSize="11" fontWeight="700" fill="#d70015">{s.proj_now_pct}%</text>
        {e > 0 && <text x={X(e - 1)} y={T + 12} fontSize="10" fill="#0a84ff" textAnchor="middle">今天</text>}
        {c.labels.map((l: string, i: number) => (
          <g key={l}><rect x={X(i) - (W - L - R) / n / 2} y={T} width={(W - L - R) / n} height={H - T - B} fill="transparent" onMouseEnter={() => setHover(i)} />
            {i % 4 === 0 && <text x={X(i)} y={H - 10} fontSize="10" textAnchor="middle" fill="#86868b">{l}</text>}</g>))}
        {hover != null && (() => {
          const rows: [string, string, number | undefined][] = [["目标", "#86868b", c.target[hover]], ["实际", "#0a84ff", c.actual[hover]],
            ["按现状", "#ff453a", val(nowPts, hover)], ["采纳后", "#30d158", val(planPts, hover)]];
          const shown = rows.filter((r) => r[2] != null);
          return <g><line x1={X(hover)} x2={X(hover)} y1={T} y2={H - B} stroke="#c7c7cc" />
            <foreignObject x={X(hover) > W / 2 ? X(hover) - 140 : X(hover) + 8} y={T + 6} width="132" height={24 + shown.length * 17}>
              <div className="imp-tip"><b>{c.labels[hover]}</b>{shown.map(([k, col, v]) => <div key={k}><i style={{ background: col }} />{k} {fmt(v!)}</div>)}</div>
            </foreignObject></g>;
        })()}
      </svg>
    </div>
  );
}

function Ask({ a, sign }: any) {
  const [state, setState] = useState<string | null>(null);
  return (
    <div className={`imp-card imp-ask ${state || ""}`}>
      <Gavel size={18} />
      <h3>{a.title}</h3>
      <p className="muted small">{a.detail}</p>
      <b className="imp-ask-cost">{money(sign, a.cost)}</b>
      {state ? <div className="imp-ask-done">{state === "yes" ? "✓ 已同意（演示）" : "已退回，请补充依据（演示）"}</div> : (
        <div className="row" style={{ gap: 8 }}>
          <button className="btn primary sm" onClick={() => setState("yes")}>同意</button>
          <button className="btn sm" onClick={() => setState("no")}>需要更多依据</button>
        </div>)}
    </div>
  );
}

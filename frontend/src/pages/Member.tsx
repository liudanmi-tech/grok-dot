import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Clock, MessageCircle, Pause, Play, Shield, Wrench } from "lucide-react";
import { api, fmtTime, post } from "../api";
import { usePoll } from "../hooks";
import { ActivityPill, Avatar, Markdown, StepList, TaskPill, timeAgo } from "../ui";
import { SAMPLES } from "./CommandCenter";

export default function Member({ id, go, onChange }: { id: string; go: (p: string) => void; onChange: () => void }) {
  const { data: b, error, reload } = usePoll<any>(`/bots/${id}`, 2000);
  const appr = usePoll<any[]>("/approvals", 3000);
  const users = usePoll<any[]>("/users", 0);
  const [tab, setTab] = useState<"work" | "profile">("work");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setTab("work"); setErr(null); }, [id]);
  if (error) return <div className="card err">{error}</div>;
  if (!b) return <div className="card muted">加载中…</div>;

  const run = async (fn: () => Promise<any>) => {
    setErr(null);
    try { await fn(); reload(); appr.reload(); onChange(); } catch (e: any) { setErr(e.message); }
  };
  const approvals = Object.fromEntries((appr.data || []).map((a: any) => [a.approval_id, a]));
  const uname = Object.fromEntries((users.data || []).map((u: any) => [u.id, u.name]));

  return (
    <>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row between" style={{ alignItems: "flex-start" }}>
          <div className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start", gap: 14 }}>
            <Avatar bot={b} size="lg" />
            <div>
              <div className="row"><h1 style={{ margin: 0, fontSize: 20 }}>{b.display_name}</h1><ActivityPill a={b.activity} /></div>
              <div className="muted">{b.title} · {b.summary}</div>
              <div className="row" style={{ marginTop: 8 }}>
                <span className="pill accent"><Shield size={11} />{b.autonomy}</span>
                <span className="tag">作用域 {Object.entries(b.scope).map(([k, v]: any) => `${k}=${v.join("/")}`).join(" · ")}</span>
                {b.triggers.map((t: any) => <span key={t.routine} className="tag"><Clock size={11} /> {t.routine} · {t.expr}</span>)}
                <span className="tag">本月 ${b.month_spend.toFixed(3)}{b.budget ? ` / $${b.budget}` : ""}</span>
              </div>
            </div>
          </div>
          <div className="row">
            {b.triggers.map((t: any) => (
              <button key={t.routine} className="btn" disabled={b.state !== "active"}
                onClick={() => run(() => post(`/routines/${b.id}/${t.routine}/run`))}><Play size={13} />立即运行 {t.routine}</button>
            ))}
            {b.state === "active"
              ? <button className="btn" onClick={() => run(() => api(`/bots/${b.id}`, { method: "PATCH", body: JSON.stringify({ state: "paused" }) }))}><Pause size={13} />暂停</button>
              : <button className="btn primary" onClick={() => run(() => api(`/bots/${b.id}`, { method: "PATCH", body: JSON.stringify({ state: "active" }) }))}><Play size={13} />恢复在岗</button>}
          </div>
        </div>
        {err && <div className="err">{err}</div>}
      </div>

      <div className="tabs">
        <button className={tab === "work" ? "on" : ""} onClick={() => setTab("work")}>工作流（{b.tasks.length}）</button>
        <button className={tab === "profile" ? "on" : ""} onClick={() => setTab("profile")}>岗位档案</button>
      </div>

      {tab === "work" ? <Work b={b} approvals={approvals} uname={uname} run={run} go={go} /> : <Profile b={b} />}
    </>
  );
}

function Work({ b, approvals, uname, run, go }: any) {
  const [text, setText] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const end = useRef<HTMLDivElement>(null);
  const tasks = useMemo(() => [...b.tasks].reverse().slice(-12), [b.tasks]);
  const last = tasks[tasks.length - 1];
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [tasks.length, last?.status, last?.progress_done]);
  const act = (tid: string, action: string, note?: string) => run(() => post(`/tasks/${tid}/action`, { action, note }));
  const decide = (aid: string, decision: string, note?: string) => run(() => post(`/approvals/${aid}/decide`, { decision, note }));
  const send = () => text.trim() && run(async () => { await post("/tasks", { bot_id: b.id, goal: text.trim() }); setText(""); });
  const whatsapp = b.channel === "WhatsApp";

  return (
    <div className="thread">
      {!tasks.length && <div className="card muted">还没有任务。{whatsapp ? "经销商在 WhatsApp 发来消息后，这里会出现它的工作记录。" : "在下面告诉它要做什么。"}</div>}
      {tasks.map((t: any) => (
        <div key={t.task_id} className="stack">
          <div className="msg me">
            <div className="av sm" style={{ background: "#334155" }}>{t.source === "routine" ? "定" : t.source === "whatsapp" ? "W" : (uname[t.requester] || "我").slice(0, 1)}</div>
            <div className="bubble">
              <div className="muted small">{t.source === "routine" ? "例程自动触发" : t.source === "whatsapp" ? "WhatsApp 经销商消息" : uname[t.requester] || t.requester} · {timeAgo(t.created_at)}</div>
              <div>{t.goal}</div>
            </div>
          </div>
          <div className="msg">
            <Avatar bot={b} size="sm" dot={false} />
            <div className="bubble" style={{ flex: 1 }}>
              <div className="row between">
                <div className="row"><b>{b.display_name}</b><TaskPill status={t.status} /><span className="muted small">计划 v{t.plan_version} · {t.progress_done}/{t.progress_total} 步</span></div>
                <span className="muted small mono">${t.cost_usd.toFixed(4)} · <a onClick={() => go(`tasks/${t.task_id}`)} style={{ cursor: "pointer" }}>审计回放</a></span>
              </div>
              <StepList steps={t.steps} />
              {t.notes.length > 0 && <div className="muted small">{t.notes[t.notes.length - 1].text}</div>}

              {t.status === "awaiting_confirmation" && (
                <div className="approval">
                  <b>这是一个大任务，需要你先确认计划</b>
                  <div className="row" style={{ marginTop: 6 }}>
                    <button className="btn primary sm" onClick={() => act(t.task_id, "confirm")}>批准计划</button>
                    <input placeholder="或者补充要求…" value={notes[t.task_id] || ""} onChange={(e) => setNotes({ ...notes, [t.task_id]: e.target.value })} />
                    <button className="btn sm" disabled={!notes[t.task_id]} onClick={() => act(t.task_id, "modify", notes[t.task_id])}>修改要求</button>
                    <button className="btn sm danger" onClick={() => act(t.task_id, "cancel")}>取消</button>
                  </div>
                </div>
              )}
              {t.status === "needs_human" && (
                <div className="approval"><b>需要人来处理这一步</b>
                  <div className="row" style={{ marginTop: 6 }}>
                    <input placeholder="处理说明" value={notes[t.task_id] || ""} onChange={(e) => setNotes({ ...notes, [t.task_id]: e.target.value })} />
                    <button className="btn sm" onClick={() => act(t.task_id, "handback", notes[t.task_id])}>已处理，交还</button>
                    <button className="btn sm" onClick={() => act(t.task_id, "takeover", notes[t.task_id])}>我来做完</button>
                  </div>
                </div>
              )}

              {t.outputs.map((o: any, i: number) => (
                <div key={i}><div className="output rich"><Markdown text={o.content} /></div><div className="muted small" style={{ marginTop: 4 }}>模型 {o.model}</div></div>
              ))}

              {t.approvals.map((aid: string) => {
                const a = approvals[aid];
                if (!a) return null;
                const open = ["pending", "escalated"].includes(a.state);
                return (
                  <div key={aid} className="approval">
                    <div className="row between">
                      <b>审批 · {a.action_class} · {String(a.target)}</b>
                      <span className="row"><span className={`pill ${a.review?.level === "low" ? "good" : a.review?.level === "reject" ? "bad" : "warn"}`}>预审 {a.review?.level}</span>
                        <span className="pill">{a.state}</span></span>
                    </div>
                    {a.payload?.args?._preview && <div className="small" style={{ marginTop: 4 }}>{a.payload.args._preview}</div>}
                    {a.review?.issues?.map((x: any, i: number) => <div key={i} className="err">{x.msg}</div>)}
                    {open && (
                      <div className="row" style={{ marginTop: 6 }}>
                        <button className="btn primary sm" onClick={() => decide(aid, "approve")}>批准</button>
                        <input placeholder="拒绝原因" value={notes[aid] || ""} onChange={(e) => setNotes({ ...notes, [aid]: e.target.value })} />
                        <button className="btn sm danger" disabled={!notes[aid]} onClick={() => decide(aid, "reject", notes[aid])}>拒绝</button>
                        <span className="muted small">审批人：{a.approver_role}</span>
                      </div>
                    )}
                    {!open && a.decision_note && <div className="muted small">{a.approver_id} · {a.decision_note}</div>}
                  </div>
                );
              })}

              {t.status === "delivered" && t.requester && (
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn sm" onClick={() => act(t.task_id, "useful")}>👍 有用</button>
                  <input placeholder="需要怎么改？" value={notes[t.task_id] || ""} onChange={(e) => setNotes({ ...notes, [t.task_id]: e.target.value })} />
                  <button className="btn sm" disabled={!notes[t.task_id]} onClick={() => act(t.task_id, "rework", notes[t.task_id])}>需修改</button>
                </div>
              )}
              <div className="muted small" style={{ marginTop: 6 }}>{fmtTime(t.updated_at)}</div>
            </div>
          </div>
        </div>
      ))}
      <div ref={end} />

      {whatsapp ? (
        <div className="card row between"><span className="muted">这名成员在 WhatsApp 上为经销商工作，由经销商消息触发。</span>
          <button className="btn" onClick={() => go("whatsapp")}><MessageCircle size={14} />打开经销商渠道</button></div>
      ) : b.state === "active" && (
        <div className="composer">
          <textarea rows={2} value={text} placeholder={`告诉 ${b.display_name} 要做什么…`} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
          <div className="row between">
            <div className="row">{(SAMPLES[b.template] || []).map((s) => <button key={s} className="btn sm" onClick={() => setText(s)}>{s.length > 26 ? s.slice(0, 26) + "…" : s}</button>)}</div>
            <button className="btn primary" disabled={!text.trim()} onClick={send}><ArrowUp size={14} />派活</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Profile({ b }: any) {
  return (
    <div className="grid g2">
      <div className="card">
        <h2>职责</h2>
        <p style={{ marginTop: 0 }}>{b.summary}</p>
        <div className="row">{b.capabilities.map((c: string) => <span key={c} className="tag">{c}</span>)}</div>
        <h3>负责人</h3>
        <div className="muted">业务负责人 {b.business_owner} · 智能体运营 {b.owner}</div>
        <div className="muted mono">服务账号 {b.service_account} · 记忆空间 {b.namespace}</div>
        <div className="muted small">招募：{b.created_by} · {fmtTime(b.created_at)}</div>
      </div>
      <div className="card">
        <h2><Wrench size={15} /> 可用工具（{b.tools.length}）</h2>
        <div className="muted small" style={{ marginBottom: 6 }}>只能调用以下工具；每次调用经工具代理判定权限并写审计。</div>
        <div className="row">{b.tools.map((t: string) => <span key={t} className="tag mono">{t}</span>)}</div>
        <h3>知识库可见范围</h3><div className="muted">{b.kb_visibility.join(" / ")}</div>
        <h3>运行限制</h3><div className="muted">单次最长 {b.limits.max_run_minutes} 分钟 · 最多 {b.limits.max_tool_calls_per_run} 次工具调用</div>
      </div>
      <div className="card">
        <h2>技能（{b.skills.length}）</h2>
        {b.skills.map((s: any) => (
          <div key={s.name} style={{ marginBottom: 10 }}>
            <div><b>{s.name}</b> <span className="muted small">v{s.version}</span></div>
            <div className="muted small">{s.description}</div>
            <ol className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>{s.steps.map((x: string) => <li key={x}>{x}</li>)}</ol>
          </div>
        ))}
      </div>
      <div className="card">
        <h2>协作</h2>
        {!b.bus_routes.length && <div className="muted">不与其他成员交换数据（对外岗位隔离）。</div>}
        {b.bus_routes.map((r: any, i: number) => (
          <div key={i} className="muted">{r.from === b.template ? "→ 转交给" : "← 接收自"} <b>{r.from === b.template ? r.to : r.from}</b> · {r.intents.join("、")}</div>
        ))}
        <h3>记忆（{b.memories.length}）</h3>
        {!b.memories.length && <div className="muted">暂无。</div>}
        {b.memories.slice(0, 8).map((m: any) => <div key={m.memory_id} className="small" style={{ padding: "3px 0" }}><span className="tag">{m.type}</span> {m.content}</div>)}
      </div>
    </div>
  );
}

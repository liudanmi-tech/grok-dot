import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowUp, CheckSquare, FileText, ListChecks, Sparkles, Target } from "lucide-react";
import { fmtTime, post } from "../api";
import { usePoll } from "../hooks";
import { Avatar, DeliverableCard, Markdown, TaskPill, timeAgo } from "../ui";
import { MemberStack } from "./Chats";

export default function Chat({ id, team, users, go }: any) {
  const { data: d, error, reload } = usePoll<any>(`/conversations/${id}`, 2500);
  const appr = usePoll<any[]>("/approvals", 4000);
  const [sel, setSel] = useState<{ task_id: string; index: number } | null>(null);
  const [tab, setTab] = useState<"deliver" | "progress" | "context">("deliver");
  const [err, setErr] = useState<string | null>(null);
  const [draft, setDraft] = useState<any>(null);
  const end = useRef<HTMLDivElement>(null);
  const n = d?.messages?.length || 0;
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [n]);
  useEffect(() => { setSel(null); setTab("deliver"); }, [id]);
  if (error) return <div className="card err">{error}</div>;
  if (!d) return <div className="card muted">加载中…</div>;

  const byId = Object.fromEntries(team.map((b: any) => [b.id, b]));
  const uname = Object.fromEntries((users || []).map((u: any) => [u.id, u.name]));
  const approvals = Object.fromEntries((appr.data || []).map((a: any) => [a.approval_id, a]));
  const t = d.task;
  const current = sel || (d.deliverables.length ? d.deliverables[d.deliverables.length - 1] : null);
  const run = async (fn: () => Promise<any>) => {
    setErr(null);
    try { await fn(); reload(); appr.reload(); } catch (e: any) { setErr(e.message); }
  };
  const open = (task_id: string, index: number) => { setSel({ task_id, index }); setTab("deliver"); };
  // 交付卡片 / 待办里的「下一步」→ 预填到群聊输入框；负责人是团队成员就自动 @
  const pushAction = (a: { text: string; owner?: string; due?: string }) => {
    const owner = a.owner || "";
    const member = team.find((b: any) => b.channel === "内部" && b.template !== "chief-of-staff"
      && owner && (owner.includes(b.display_name.split(" · ")[0]) || b.display_name.includes(owner)));
    setDraft({ text: `请推进：${a.text}${owner ? `（负责：${owner}` : ""}${a.due ? `${owner ? "，" : "（"}时间：${a.due}` : ""}${owner || a.due ? "）" : ""}`,
      to: member?.id || null, action: a.text, n: Date.now() });
  };

  return (
    <div className="chat-page">
      <div className="chat-head">
        <button className="btn ghost sm" onClick={() => go("chats")}><ArrowLeft size={14} />全部对话</button>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row" style={{ flexWrap: "nowrap" }}><b className="ellipsis" style={{ fontSize: 16 }}>{t.goal}</b><TaskPill status={t.status} /></div>
          <div className="muted small row">
            <MemberStack ids={d.members} byId={byId} max={8} /><span>{d.members.length} 名成员</span>
            <span>· {fmtTime(t.created_at)}</span>
            {d.kpi && <span className="tag" style={{ cursor: "pointer" }} onClick={() => go(`kpi/${d.kpi.kpi_id}`)}><Target size={11} /> {d.kpi.title}</span>}
          </div>
        </div>
      </div>

      <div className="chat-split">
        <div className="chat-left">
          <div className="chat-stream">
            {d.messages.map((m: any, i: number) => <Msg key={i} m={m} byId={byId} uname={uname} approvals={approvals} run={run}
              open={open} current={current} children={d.children} />)}
            <div ref={end} />
          </div>
          {err && <div className="err">{err}</div>}
          <Composer t={t} team={team} run={run} draft={draft} />
        </div>

        <div className="chat-right">
          <div className="tabs" style={{ marginBottom: 10 }}>
            <button className={tab === "deliver" ? "on" : ""} onClick={() => setTab("deliver")}><FileText size={13} /> 交付物（{d.deliverables.length}）</button>
            <button className={tab === "progress" ? "on" : ""} onClick={() => setTab("progress")}><ListChecks size={13} /> 进度与待办</button>
            <button className={tab === "context" ? "on" : ""} onClick={() => setTab("context")}>上下文与记忆</button>
          </div>
          {tab === "deliver" && (
            !d.deliverables.length ? <div className="panel-empty">成员交付后，HTML 交付卡片会显示在这里。</div> : (
              <>
                <div className="deliver-tabs">
                  {d.deliverables.map((x: any) => (
                    <span key={x.task_id + x.index} className={`mention ${current?.task_id === x.task_id && current?.index === x.index ? "on" : ""}`}
                      onClick={() => open(x.task_id, x.index)}>
                      <Avatar bot={byId[x.bot_id]} size="sm" dot={false} />{x.summary ? "汇总" : x.bot_name}
                    </span>
                  ))}
                </div>
                {current && (() => {
                  const x = d.deliverables.find((y: any) => y.task_id === current.task_id && y.index === current.index);
                  return x?.has_card ? <DeliverableCard key={x.task_id + x.index} taskId={x.task_id} index={x.index} fallback={x.content} fold={false} onAction={pushAction} />
                    : x && <div className="output rich"><Markdown text={x.content} /></div>;
                })()}
              </>
            ))}
          {tab === "progress" && <Progress d={d} byId={byId} go={go} pushAction={pushAction} />}
          {tab === "context" && <Context d={d} byId={byId} uname={uname} go={go} />}
        </div>
      </div>
    </div>
  );
}

function Msg({ m, byId, uname, approvals, run, open, current, children }: any) {
  if (m.kind === "system") return <div className="msg-sys">{m.text} · {timeAgo(m.ts)}</div>;
  if (m.kind === "user") {
    return (
      <div className="cmsg me">
        <div className="cbubble me">
          <div className="small muted">{uname[m.author] || m.author} · {timeAgo(m.ts)}</div>
          {m.mention && <span className="mention-tag">@{byId[m.mention]?.display_name || m.mention}</span>}{m.text}
        </div>
      </div>
    );
  }
  const b = byId[m.author] || { display_name: m.author };
  const kid = children.find((k: any) => k.task_id === m.task_id);
  return (
    <div className="cmsg">
      <Avatar bot={b} size="sm" dot={false} />
      <div className="cbody">
        <div className="small"><b>{b.display_name}</b> <span className="muted">{timeAgo(m.ts)}</span></div>
        {m.kind === "deliverable" ? (
          <div className={`deliv-chip ${current?.task_id === m.task_id && current?.index === m.index ? "on" : ""}`} onClick={() => open(m.task_id, m.index)}>
            <FileText size={16} />
            <div style={{ minWidth: 0 }}><div><b>{m.title}</b></div><div className="small muted">{m.text}</div></div>
            <span className="small" style={{ marginLeft: "auto", whiteSpace: "nowrap" }}>查看 →</span>
          </div>
        ) : m.kind === "approval" ? (
          <ApprovalInline a={approvals[m.approval_id]} text={m.text} run={run} />
        ) : m.kind === "alert" ? (
          <div className="cbubble alert">
            {m.text}
            {m.status === "needs_human" && kid?.status === "needs_human" && (
              <div className="row" style={{ marginTop: 6 }}>
                <button className="btn sm" onClick={() => run(() => post(`/tasks/${m.task_id}/action`, { action: "handback", note: "已处理" }))}>已处理，交还</button>
                <button className="btn sm" onClick={() => run(() => post(`/tasks/${m.task_id}/action`, { action: "takeover", note: "人工完成" }))}>我来做完</button>
              </div>
            )}
          </div>
        ) : (
          <div className="cbubble">
            <span style={{ whiteSpace: "pre-wrap" }}>{m.text}</span>
            {m.assign?.length > 0 && (
              <div className="assign">
                {m.assign.map((a: any) => {
                  const k = children.find((x: any) => x.task_id === a.task_id);
                  return (
                    <div key={a.task_id} className="assign-row">
                      <Avatar bot={byId[a.bot_id]} size="sm" dot={false} />
                      <div style={{ minWidth: 0, flex: 1 }}><b>@{byId[a.bot_id]?.display_name}</b> <span className="small">{a.goal}</span></div>
                      {k && <TaskPill status={k.status} />}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ApprovalInline({ a, text, run }: any) {
  const [note, setNote] = useState("");
  if (!a) return <div className="cbubble">{text}</div>;
  const openState = ["pending", "escalated"].includes(a.state);
  return (
    <div className="approval" style={{ marginTop: 4 }}>
      <div className="row between"><b><CheckSquare size={13} /> 审批 · {a.action_class}</b><span className="pill">{a.state}</span></div>
      {a.payload?.args?._preview && <div className="small" style={{ marginTop: 4 }}>{a.payload.args._preview}</div>}
      {openState && (
        <div className="row" style={{ marginTop: 6 }}>
          <button className="btn primary sm" onClick={() => run(() => post(`/approvals/${a.approval_id}/decide`, { decision: "approve" }))}>批准</button>
          <input placeholder="拒绝原因" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="btn sm danger" disabled={!note} onClick={() => run(() => post(`/approvals/${a.approval_id}/decide`, { decision: "reject", note }))}>拒绝</button>
          <span className="muted small">审批人：{a.approver_role}</span>
        </div>
      )}
    </div>
  );
}

function Composer({ t, team, run, draft }: any) {
  const [text, setText] = useState("");
  const [to, setTo] = useState<string | null>(null);
  const [action, setAction] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!draft) return;
    setText(draft.text); setTo(draft.to); setAction(draft.action);
    setTimeout(() => input.current?.focus(), 0);
  }, [draft]);
  const lead = team.find((b: any) => b.id === t.bot_id)?.template === "chief-of-staff";
  const closed = ["cancelled", "failed"].includes(t.status);
  if (closed) return <div className="muted small" style={{ padding: 8 }}>对话已结束（{t.status_label}）。</div>;
  if (!lead) {
    return t.status === "delivered" ? (
      <div className="chat-composer">
        <input ref={input} value={text} placeholder="需要怎么改？（发送即要求修改）" onChange={(e) => setText(e.target.value)} />
        <div className="row between">
          <button className="btn sm" onClick={() => run(() => post(`/tasks/${t.task_id}/action`, { action: "useful" }))}>👍 有用，验收</button>
          <button className="btn primary sm" disabled={!text.trim()} onClick={() => run(async () => { await post(`/tasks/${t.task_id}/action`, { action: "rework", note: text }); setText(""); })}><ArrowUp size={13} />要求修改</button>
        </div>
      </div>
    ) : <div className="muted small" style={{ padding: 8 }}>成员执行中，完成后可验收或要求修改。</div>;
  }
  const members = team.filter((b: any) => b.state === "active" && b.channel === "内部" && b.template !== "chief-of-staff");
  const send = () => text.trim() && run(async () => {
    await post(`/tasks/${t.task_id}/followup`, { text: text.trim(), bot_id: to || undefined, from_action: action || undefined });
    setText(""); setTo(null); setAction(null);
  });
  return (
    <div className="chat-composer">
      <div className="row" style={{ gap: 4 }}>
        <span className={`mention ${!to ? "on" : ""}`} onClick={() => setTo(null)}><Sparkles size={12} style={{ marginLeft: 4 }} />总调度安排</span>
        {members.map((b: any) => (
          <span key={b.id} className={`mention ${to === b.id ? "on" : ""}`} onClick={() => setTo(b.id)}><Avatar bot={b} size="sm" dot={false} />@{b.display_name}</span>
        ))}
      </div>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input ref={input} style={{ flex: 1 }} value={text} placeholder={to ? "在群里给 TA 追加任务…" : "在群里追加要求，总调度决定找谁…"}
          onChange={(e) => { setText(e.target.value); if (!e.target.value) setAction(null); }} onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
        <button className="btn primary sm" disabled={!text.trim()} onClick={send}><ArrowUp size={13} />发送</button>
        {t.status === "delivered" && t.requester && <button className="btn sm" onClick={() => run(() => post(`/tasks/${t.task_id}/action`, { action: "useful" }))}>👍 验收</button>}
      </div>
    </div>
  );
}

function Progress({ d, byId, go, pushAction }: any) {
  const assigns = d.todos.filter((x: any) => x.kind === "assignment");
  const actions = d.todos.filter((x: any) => x.kind === "action");
  return (
    <div className="stack">
      <div>
        <h3>群成员进度</h3>
        {!d.children.length && <div className="muted small">单人任务：{byId[d.task.bot_id]?.display_name} · {d.task.progress_done}/{d.task.progress_total} 步</div>}
        <div className="lanes">
          {d.children.map((k: any) => (
            <div key={k.task_id} className="lane" onClick={() => go(`bot/${k.bot_id}`)}>
              <div className="row" style={{ flexWrap: "nowrap" }}><Avatar bot={byId[k.bot_id]} size="sm" /><b>{byId[k.bot_id]?.display_name}</b>
                <span style={{ marginLeft: "auto" }}><TaskPill status={k.status} /></span></div>
              <div className="small lane-goal">{k.goal}</div>
              <div className="bar"><i style={{ width: `${k.progress_total ? (100 * k.progress_done) / k.progress_total : 5}%` }} /></div>
            </div>
          ))}
        </div>
      </div>
      <div>
        <h3>待办（{assigns.filter((a: any) => !["delivered", "accepted"].includes(a.status)).length + actions.length}）</h3>
        <div className="todo">
          {assigns.map((a: any, i: number) => (
            <div key={"a" + i} className={`todo-item ${["delivered", "accepted"].includes(a.status) ? "done" : ""}`}>
              <span className="box" />
              <div><b>@{byId[a.bot_id]?.display_name}</b> {a.text}<div className="muted small">{a.status_label}</div></div>
            </div>
          ))}
          {actions.map((a: any, i: number) => (
            <div key={"b" + i} className={`todo-item click ${a.status === "dispatched" ? "sent" : ""}`} onClick={() => pushAction(a)}>
              <span className="box" />
              <div style={{ flex: 1 }}>{a.text}<div className="muted small">{a.owner || "—"}{a.due ? ` · ${a.due}` : ""} · 来自 {byId[a.bot_id]?.display_name} 的交付</div></div>
              <span className="small go">{a.status === "dispatched" ? "已派发" : "继续推进 →"}</span>
            </div>
          ))}
          {!assigns.length && !actions.length && <div className="muted small">暂无待办。</div>}
        </div>
      </div>
    </div>
  );
}

function Context({ d, byId, uname, go }: any) {
  const t = d.task;
  return (
    <div className="stack small">
      <table className="tbl"><tbody>
        <tr><td className="muted" style={{ width: 110 }}>目标</td><td>{t.goal}</td></tr>
        <tr><td className="muted">下达人</td><td>{uname[t.requester] || t.requester || t.source}</td></tr>
        <tr><td className="muted">数据权限快照</td><td className="mono">{JSON.stringify(t.requester_scope)}</td></tr>
        {d.kpi && <tr><td className="muted">关联 KPI</td><td><a style={{ cursor: "pointer" }} onClick={() => go(`kpi/${d.kpi.kpi_id}`)}>{d.kpi.title}</a></td></tr>}
        <tr><td className="muted">费用</td><td>${d.summary.cost_usd.toFixed(4)}</td></tr>
        <tr><td className="muted">审计</td><td><a style={{ cursor: "pointer" }} onClick={() => go(`tasks/${t.task_id}`)}>审计回放 · {t.trace_id}</a></td></tr>
        {t.context?.followups?.length > 0 && <tr><td className="muted">追加要求</td><td>{t.context.followups.map((f: any) => f.text).join("；")}</td></tr>}
      </tbody></table>
      <h3>成员记忆（执行时会召回）</h3>
      {!d.memories.length && <div className="muted">暂无。</div>}
      {d.memories.map((m: any, i: number) => (
        <div key={i} className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
          {m.bot_id ? <Avatar bot={byId[m.bot_id]} size="sm" dot={false} /> : <div className="av sm" style={{ background: "#334155" }}>我</div>}
          <div><span className="tag">{m.type}</span> {m.content}</div>
        </div>
      ))}
    </div>
  );
}

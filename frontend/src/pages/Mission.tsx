import { useState } from "react";
import { ArrowUp, Check, Loader2, Sparkles, X } from "lucide-react";
import { Avatar, Markdown, TaskPill, timeAgo } from "../ui";

// 总调度的一个任务 = 一个「作战群」：分工泳道 + 汇总交付 + 群里 @成员 追加
export function MissionBody({ t, team, go, run, post }: any) {
  const byId = Object.fromEntries(team.map((b: any) => [b.id, b]));
  const ctx = t.context || {};
  const synth = t.steps[t.steps.length - 1];
  const plan = t.steps[0];
  const rounds = new Map<number, any[]>();
  for (const a of ctx.assignments || []) rounds.set(a.round || 0, [...(rounds.get(a.round || 0) || []), a]);
  const kid = Object.fromEntries((t.children || []).map((c: any) => [c.task_id, c]));
  const done = (t.children || []).filter((c: any) => ["delivered", "accepted"].includes(c.status)).length;

  return (
    <>
      <div className="mission-row">
        <span className={`ic ${plan?.status || "pending"}`}>{plan?.status === "done" ? <Check size={13} /> : <Loader2 size={13} className="spin" />}</span>
        <span><b>分工</b>{ctx.brief ? ` · ${ctx.brief}` : ""}</span>
        {(t.children || []).length > 0 && <span className="muted small" style={{ marginLeft: "auto" }}>{done}/{t.children.length} 名成员已交付</span>}
      </div>
      {[...rounds.entries()].map(([r, list]) => (
        <div key={r}>
          {r > 0 && <div className="muted small" style={{ margin: "8px 0 2px" }}>追加 · {ctx.followups?.[r - 1]?.text}</div>}
          <div className="lanes">
            {list.map((a: any) => {
              const c = kid[a.task_id] || {};
              const b = byId[a.bot_id] || { id: a.bot_id, display_name: a.bot_id };
              const pct = c.progress_total ? (100 * c.progress_done) / c.progress_total : 5;
              return (
                <div key={a.task_id} className="lane" onClick={() => go(`bot/${a.bot_id}`)} title="打开该成员的工作流">
                  <div className="row" style={{ flexWrap: "nowrap" }}>
                    <Avatar bot={b} size="sm" dot={false} />
                    <b style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{b.display_name}</b>
                    <span style={{ marginLeft: "auto" }}>{c.status && <TaskPill status={c.status} />}</span>
                  </div>
                  <div className="small lane-goal">{a.goal}</div>
                  <div className="bar"><i style={{ width: `${pct}%`, background: c.status === "failed" ? "var(--bad)" : undefined }} /></div>
                  <div className="muted small lane-note">
                    {c.approvals?.length && c.status === "awaiting_approval" ? "⚠ 等审批 · " : ""}
                    {c.preview ? c.preview.replace(/[#*|`>-]/g, " ").slice(0, 90) + "…" : c.last_note || "排队中"}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <div className="mission-row" style={{ marginTop: 8 }}>
        <span className={`ic ${synth?.status || "pending"}`}>
          {synth?.status === "done" ? <Check size={13} /> : synth?.status === "running" ? <Loader2 size={13} className="spin" />
            : synth?.status === "failed" ? <X size={13} /> : <Sparkles size={13} />}
        </span>
        <span><b>汇总交付</b> <span className="muted small">{synth?.status === "running" ? "正在汇总各成员结果…" : synth?.status === "done" ? "" : "等所有成员交付后自动汇总"}</span></span>
      </div>
      <Followup t={t} team={team} run={run} post={post} />
    </>
  );
}

function Followup({ t, team, run, post }: any) {
  const [text, setText] = useState("");
  const [to, setTo] = useState<string | null>(null);
  if (["cancelled", "failed", "registered", "planning"].includes(t.status)) return null;
  const members = team.filter((b: any) => b.state === "active" && b.channel === "内部" && b.template !== "chief-of-staff");
  const send = () => text.trim() && run(async () => {
    await post(`/tasks/${t.task_id}/followup`, { text: text.trim(), bot_id: to || undefined });
    setText(""); setTo(null);
  });
  return (
    <div className="followup">
      <div className="row" style={{ gap: 4 }}>
        <span className={`mention ${!to ? "on" : ""}`} onClick={() => setTo(null)}><Sparkles size={12} style={{ marginLeft: 4 }} />交给总调度安排</span>
        {members.map((b: any) => (
          <span key={b.id} className={`mention ${to === b.id ? "on" : ""}`} onClick={() => setTo(b.id)}>
            <Avatar bot={b} size="sm" dot={false} />@{b.display_name}
          </span>
        ))}
      </div>
      <div className="row" style={{ flexWrap: "nowrap", marginTop: 6 }}>
        <input style={{ flex: 1 }} value={text} placeholder={to ? "在这个任务里给 TA 追加…" : "追加要求，总调度会决定找谁…"}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
        <button className="btn sm primary" disabled={!text.trim()} onClick={send}><ArrowUp size={13} />追加</button>
      </div>
    </div>
  );
}

export function ParentChip({ t, go }: any) {
  if (!t.parent) return null;
  return (
    <span className="pill accent" style={{ cursor: "pointer" }} onClick={() => go(`bot/${t.parent.bot_id}`)} title={t.parent.goal}>
      <Sparkles size={11} />{t.parent.bot_name} 分派 · {timeAgo(t.created_at)}
    </span>
  );
}

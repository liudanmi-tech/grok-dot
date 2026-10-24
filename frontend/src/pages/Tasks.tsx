import { useEffect, useState } from "react";
import { api, fmtTime, post, STATUS } from "../api";
import { Markdown } from "../ui";
import { usePoll } from "../hooks";

const ACTIONS: Record<string, [string, string][]> = {
  awaiting_confirmation: [["confirm", "批准计划"], ["modify", "修改要求"], ["cancel", "取消"]],
  running: [["pause", "暂停"], ["cancel", "取消"]],
  paused: [["resume", "继续"], ["topup", "追加预算"], ["cancel", "取消"]],
  needs_human: [["handback", "已完成，交还"], ["takeover", "我来做完"]],
  delivered: [["useful", "有用"], ["rework", "需修改"]],
};
const NEED_NOTE = new Set(["modify", "rework"]);

function pill(status: string) {
  const cls = ["delivered", "accepted"].includes(status) ? "good"
    : ["failed", "cancelled"].includes(status) ? "bad"
    : ["awaiting_confirmation", "needs_human", "paused", "awaiting_approval"].includes(status) ? "warn" : "info";
  return <span className={`pill ${cls}`}>{STATUS[status] || status}</span>;
}

export default function Tasks({ focus, setFocus }: { focus: string | null; setFocus: (id: string | null) => void }) {
  const list = usePoll<any[]>("/tasks", 3000);
  const bots = usePoll<any[]>("/bots", 0);
  const [botId, setBotId] = useState("biz-review");
  const [goal, setGoal] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const create = async () => {
    setErr(null);
    try {
      const t = await post("/tasks", { bot_id: botId, goal });
      setGoal("");
      setFocus(t.task_id);
      list.reload();
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <>
      <header className="top"><h2>任务</h2></header>
      <div className="card">
        <div className="row">
          <select value={botId} onChange={(e) => setBotId(e.target.value)}>
            {bots.data?.map((b) => <option key={b.id} value={b.id}>@{b.display_name}</option>)}
          </select>
          <input style={{ flex: 1, minWidth: 260 }} placeholder="例如：分析拉各斯 X6851 上周动销下滑原因，周三前给结论"
            value={goal} onChange={(e) => setGoal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && goal && create()} />
          <button className="btn primary" disabled={!goal} onClick={create}>下达任务</button>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>数据范围 = 岗位权限 ∩ 当前用户权限；产出只发给下达人本人。</div>
        {err && <div className="err">{err}</div>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: focus ? "minmax(0,1fr) minmax(0,1.3fr)" : "1fr", gap: 12 }}>
        <div className="card">
          <table>
            <thead><tr><th>任务</th><th>岗位</th><th>状态</th><th>进度</th><th>创建</th></tr></thead>
            <tbody>
              {list.data?.map((t) => (
                <tr key={t.task_id} className="click" onClick={() => setFocus(t.task_id)}
                  style={t.task_id === focus ? { background: "var(--accent-soft)" } : undefined}>
                  <td>{t.goal.slice(0, 40)}<div className="muted">{t.source === "routine" ? "例程" : t.requester}</div></td>
                  <td className="muted">{t.bot_id}</td>
                  <td>{pill(t.status)}</td>
                  <td>{t.progress_done}/{t.progress_total}</td>
                  <td className="muted">{fmtTime(t.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {focus && <TaskDetail id={focus} onClose={() => setFocus(null)} />}
      </div>
    </>
  );
}

function TaskDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: t, reload } = usePoll<any>(`/tasks/${id}`, 2000);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setErr(null); setNote(""); }, [id]);
  if (!t) return <div className="card muted">加载中…</div>;

  const act = async (action: string) => {
    setErr(null);
    try {
      await post(`/tasks/${id}/action`, { action, note: note || undefined });
      setNote("");
      reload();
    } catch (e: any) { setErr(e.message); }
  };
  const actions = ACTIONS[t.status] || [];

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{t.goal}</b><button className="btn" onClick={onClose}>关闭</button>
      </div>
      <div className="row muted" style={{ marginTop: 6 }}>
        {pill(t.status)} <span>{t.bot_id}</span> <span>技能 {t.skill}</span>
        <span>计划 v{t.plan_version}</span> <span>费用 ${t.cost_usd.toFixed(3)} / ${t.budget_usd}</span>
        <span>trace {t.trace_id}</span>
      </div>
      {t.requester_scope && <div className="muted">下达人权限快照：{JSON.stringify(t.requester_scope)}</div>}

      <h3>计划与进度（{t.progress_done}/{t.progress_total}）</h3>
      <ul className="steps">
        {t.steps.map((s: any) => (
          <li key={s.index} className={s.status}>{s.title}
            <span className="muted"> · {STATUS[s.status] || s.status}{s.tool ? ` · ${s.tool}` : ""}{s.note ? ` · ${s.note}` : ""}</span></li>
        ))}
      </ul>

      {actions.length > 0 && (
        <>
          {actions.some(([a]) => NEED_NOTE.has(a)) || t.status === "needs_human" ? (
            <input style={{ width: "100%", marginBottom: 6 }} placeholder="说明（修改要求 / 需修改意见 / 处理说明）"
              value={note} onChange={(e) => setNote(e.target.value)} />
          ) : null}
          <div className="row">{actions.map(([a, label]) => (
            <button key={a} className={`btn ${a === "confirm" || a === "useful" ? "primary" : a === "cancel" ? "danger" : ""}`}
              disabled={NEED_NOTE.has(a) && !note} onClick={() => act(a)}>{label}</button>
          ))}</div>
        </>
      )}
      {err && <div className="err">{err}</div>}

      {t.outputs.length > 0 && <h3>产出</h3>}
      {t.outputs.map((o: any, i: number) => (
        <div key={i}><div className="muted">{o.step} · {o.model}</div><div className="output rich"><Markdown text={o.content} /></div></div>
      ))}

      {t.approvals.length > 0 && <><h3>关联审批</h3><div className="muted">{t.approvals.join("、")}</div></>}

      <h3>动态</h3>
      <ul className="steps">{t.notes.slice().reverse().map((n: any, i: number) => (
        <li key={i} className="done">{n.text} <span className="muted">· {fmtTime(n.ts)}</span></li>
      ))}</ul>

      <h3>审计链路回放（{t.timeline.length} 条）</h3>
      <table><tbody>{t.timeline.map((e: any) => (
        <tr key={e.event_id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.ts)}</td>
          <td>{e.event_type}</td>
          <td className="muted">{summary(e)}</td></tr>
      ))}</tbody></table>
    </div>
  );
}

function summary(e: any) {
  const p = e.payload || {};
  if (e.event_type === "tool.call") return `${p.tool} · ${p.decision}`;
  if (e.event_type === "model.call") return `${p.model} · ${p.input_tokens}/${p.output_tokens} tokens · $${p.cost_usd}`;
  if (e.event_type === "model.fallback") return `${p.from_model} → ${p.to_model}`;
  if (e.event_type === "task.status") return `${STATUS[p.from_status] || p.from_status} → ${STATUS[p.to_status] || p.to_status}${p.note ? " · " + p.note : ""}`;
  if (e.event_type === "policy.deny") return `${p.tool || ""} · ${p.error || p.kind}`;
  return p.tool || p.kind || p.approval_id || "";
}

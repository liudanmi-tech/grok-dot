import { useState } from "react";
import { ArrowUp, MessageSquarePlus, Sparkles } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { Avatar, TaskPill, timeAgo } from "../ui";

const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function dayLabel(day: string, today?: string) {
  if (today && day === today) return "今天";
  const d = new Date(day + "T12:00:00");
  return `${day.slice(5).replace("-", "月")}日 ${WEEK[d.getDay()]}`;
}

export function MemberStack({ ids, byId, max = 5 }: { ids: string[]; byId: Record<string, any>; max?: number }) {
  return (
    <span className="stack-av">
      {ids.slice(0, max).map((id) => <Avatar key={id} bot={byId[id] || { display_name: id }} size="sm" dot={false} />)}
      {ids.length > max && <span className="more">+{ids.length - max}</span>}
    </span>
  );
}

export function ConvRow({ c, byId, go }: any) {
  const pct = c.progress_total ? (100 * c.progress_done) / c.progress_total : 0;
  return (
    <div className={`conv-row ${c.attention ? "attn" : ""}`} onClick={() => go(`chat/${c.task_id}`)}>
      <MemberStack ids={c.members} byId={byId} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <b className="ellipsis">{c.goal}</b>
          <span style={{ marginLeft: "auto" }} className="row"><TaskPill status={c.status} /></span>
        </div>
        <div className="muted small ellipsis">{c.preview || "…"}</div>
        <div className="row small muted" style={{ gap: 10 }}>
          <span className="bar" style={{ width: 90 }}><i style={{ width: `${pct}%` }} /></span>
          <span>{c.progress_done}/{c.progress_total}</span>
          <span>{c.members.length} 名成员</span>
          {c.deliverables > 0 && <span>{c.deliverables} 份交付</span>}
          {c.kpi_id && <span className="tag">KPI</span>}
          <span style={{ marginLeft: "auto" }}>{timeAgo(c.updated_at)}</span>
        </div>
      </div>
    </div>
  );
}

export default function Chats({ team, go }: any) {
  const { data, reload } = usePoll<any[]>("/conversations", 3000);
  const clock = usePoll<any>("/demo/clock", 10000);
  const byId = Object.fromEntries(team.map((b: any) => [b.id, b]));
  const today = clock.data?.now?.slice(0, 10);
  const groups = new Map<string, any[]>();
  for (const c of data || []) groups.set(c.day, [...(groups.get(c.day) || []), c]);

  return (
    <>
      <div className="page-head">
        <div><h1>任务对话</h1><p>每个任务是一个群：你、总调度和参与的数字员工。总调度拆分工、成员并行干活、交付物在右侧面板查看。</p></div>
      </div>
      <NewChat team={team} go={go} onDone={reload} />
      {!data?.length && <div className="card muted">还没有任务对话。</div>}
      {[...groups.entries()].map(([day, list]) => (
        <div key={day} style={{ marginBottom: 14 }}>
          <div className="day-sep">{dayLabel(day, today)}<span>{list.length} 个对话</span></div>
          <div className="card" style={{ padding: 4 }}>{list.map((c) => <ConvRow key={c.task_id} c={c} byId={byId} go={go} />)}</div>
        </div>
      ))}
    </>
  );
}

function NewChat({ team, go, onDone }: any) {
  const members = team.filter((b: any) => b.state === "active" && b.channel === "内部" && b.template !== "chief-of-staff");
  const chief = team.find((b: any) => b.template === "chief-of-staff");
  const [sel, setSel] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    if (!text.trim()) return;
    setBusy(true); setErr(null);
    try {
      // 只选一名成员 → 直接派给 TA；否则由总调度在选定成员（或全体）里分工
      const body = sel.length === 1 ? { bot_id: sel[0], goal: text.trim() }
        : { bot_id: chief?.id || "chief-of-staff", goal: text.trim(), members: sel.length ? sel : undefined };
      const t = await post("/tasks", body);
      setText(""); setSel([]); onDone(); go(`chat/${t.task_id}`);
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="composer" style={{ marginBottom: 18 }}>
      <div className="row small" style={{ marginBottom: 6 }}>
        <MessageSquarePlus size={14} /><b>新建对话</b>
        <span className="muted">群成员：</span>
        <span className={`mention ${!sel.length ? "on" : ""}`} onClick={() => setSel([])}><Sparkles size={12} style={{ marginLeft: 4 }} />总调度自动选人</span>
        {members.map((b: any) => (
          <span key={b.id} className={`mention ${sel.includes(b.id) ? "on" : ""}`}
            onClick={() => setSel(sel.includes(b.id) ? sel.filter((x) => x !== b.id) : [...sel, b.id])}>
            <Avatar bot={b} size="sm" dot={false} />{b.display_name}
          </span>
        ))}
      </div>
      <textarea rows={2} value={text} placeholder={sel.length === 1 ? "直接派给这名成员…" : "说一句目标，总调度会拆成分工派给群成员…"}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
      <div className="row between">
        <span className="muted small">数据范围 = 成员权限 ∩ 你的权限 · 只在需要你决策时打扰你</span>
        <button className="btn primary" disabled={!text.trim() || busy} onClick={send}><ArrowUp size={14} />{busy ? "创建中…" : "开始对话"}</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}

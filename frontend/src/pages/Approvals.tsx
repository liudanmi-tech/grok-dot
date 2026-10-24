import { useState } from "react";
import { fmtTime, post, STATUS } from "../api";
import { usePoll } from "../hooks";

const LEVEL: Record<string, [string, string]> = {
  low: ["good", "低风险"], attention: ["warn", "需关注"], reject: ["bad", "预审退回"],
};

export default function Approvals() {
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const open = usePoll<any[]>(`/approvals?state=pending,escalated${scope === "mine" ? "&mine=true" : ""}`, 3000);
  const done = usePoll<any[]>("/approvals?state=executed,rejected,expired,returned,failed", 5000);
  const [err, setErr] = useState<string | null>(null);
  const lows = (open.data || []).filter((a) => a.review?.level === "low").map((a) => a.approval_id);

  const batch = async () => {
    setErr(null);
    try { await post("/approvals/batch-approve", { ids: lows }); open.reload(); done.reload(); }
    catch (e: any) { setErr(e.message); }
  };

  return (
    <>
      <header className="top">
        <h2>审批</h2>
        <div className="row">
          <select value={scope} onChange={(e) => setScope(e.target.value as any)}>
            <option value="mine">我可以审批的</option><option value="all">全部待审批</option>
          </select>
          <button className="btn" disabled={!lows.length} onClick={batch}>批量批准低风险（{lows.length}）</button>
        </div>
      </header>
      {err && <div className="err">{err}</div>}
      {!open.data?.length && <div className="card muted">当前没有待审批事项。</div>}
      {open.data?.map((a) => <ApprovalCard key={a.approval_id} a={a} onDone={() => { open.reload(); done.reload(); }} />)}

      <h3>已处理</h3>
      <div className="card">
        <table>
          <thead><tr><th>动作</th><th>对象</th><th>状态</th><th>预审</th><th>审批人</th><th>说明</th><th>时间</th></tr></thead>
          <tbody>{done.data?.map((a) => (
            <tr key={a.approval_id}>
              <td>{a.action_class}</td><td>{String(a.target)}</td>
              <td><span className="pill">{STATUS[a.state] || a.state}</span></td>
              <td>{a.review && <span className={`pill ${LEVEL[a.review.level]?.[0]}`}>{LEVEL[a.review.level]?.[1]}</span>}</td>
              <td className="muted">{a.approver_id || "—"}</td>
              <td className="muted">{a.decision_note || (a.review?.issues || []).map((i: any) => i.msg).join("；")}</td>
              <td className="muted">{fmtTime(a.decided_at || a.created_at)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}

function ApprovalCard({ a, onDone }: { a: any; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [editing, setEditing] = useState(false);
  const [edited, setEdited] = useState(JSON.stringify(a.payload.args, null, 2));
  const [err, setErr] = useState<string | null>(null);
  const lv = LEVEL[a.review?.level] || ["", ""];

  const decide = async (decision: string) => {
    setErr(null);
    try {
      const body: any = { decision, note: note || undefined };
      if (decision === "edit") body.edited_args = JSON.parse(edited);
      await post(`/approvals/${a.approval_id}/decide`, body);
      onDone();
    } catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{a.action_class} · {String(a.target)}</b>
        <span className="row">
          <span className={`pill ${lv[0]}`}>{lv[1]}</span>
          <span className="pill">{STATUS[a.state]}</span>
          <span className="muted">过期 {fmtTime(a.expires_at)}</span>
        </span>
      </div>
      <div className="muted">发起：{a.bot_id} · 审批角色 {a.approver_role}（备份 {a.backup_role}）· {a.approval_id}</div>
      {a.review?.issues?.length > 0 && (
        <ul>{a.review.issues.map((i: any, k: number) => <li key={k} className="err">{i.msg}</li>)}</ul>
      )}
      <h3>待执行内容</h3>
      {editing ? <textarea style={{ minHeight: 140 }} value={edited} onChange={(e) => setEdited(e.target.value)} />
        : <pre>{a.payload.args._preview ? a.payload.args._preview + "\n\n" : ""}{JSON.stringify(
            Object.fromEntries(Object.entries(a.payload.args).filter(([k]) => !k.startsWith("_"))), null, 2)}</pre>}
      {a.evidence?.length > 0 && (
        <details><summary className="muted">依据（{a.evidence.length}）</summary>
          {a.evidence.map((e: any) => <pre key={e.ref}>{e.ref}{"\n"}{e.text}</pre>)}
        </details>
      )}
      <input style={{ width: "100%", margin: "8px 0" }} placeholder="说明（拒绝时必填）" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="row">
        <button className="btn primary" onClick={() => decide("approve")}>批准</button>
        {editing ? <button className="btn" onClick={() => decide("edit")}>保存修改并批准</button>
          : <button className="btn" onClick={() => setEditing(true)}>修改后批准</button>}
        <button className="btn danger" disabled={!note} onClick={() => decide("reject")}>拒绝</button>
      </div>
      {err && <div className="err">{err}</div>}
    </div>
  );
}

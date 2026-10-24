import { useState } from "react";
import { fmtTime } from "../api";
import { usePoll } from "../hooks";

export default function Audit() {
  const [trace, setTrace] = useState("");
  const [type, setType] = useState("");
  const q = new URLSearchParams({ limit: "300", ...(trace ? { trace_id: trace } : {}), ...(type ? { event_type: type } : {}) });
  const list = usePoll<any[]>(`/audit?${q}`, 5000);
  const verify = usePoll<any>("/audit/verify", 10000);

  return (
    <>
      <header className="top"><h2>审计</h2>
        <span className={`pill ${verify.data?.ok ? "good" : "bad"}`}>
          哈希链{verify.data ? (verify.data.ok ? `完整 · ${verify.data.checked} 条` : `断链 · 第 ${verify.data.broken_at_seq} 条`) : "…"}
        </span>
      </header>
      <div className="card row">
        <input placeholder="trace_id" value={trace} onChange={(e) => setTrace(e.target.value)} />
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">全部事件</option>
          {["tool.call", "policy.deny", "model.call", "model.fallback", "approval.create", "approval.decide",
            "task.status", "memory.write", "memory.blocked", "bus.send", "alert"].map((t) => <option key={t}>{t}</option>)}
        </select>
      </div>
      <div className="card">
        <table>
          <thead><tr><th>#</th><th>时间</th><th>事件</th><th>Bot</th><th>trace</th><th>内容</th></tr></thead>
          <tbody>{list.data?.map((e) => (
            <tr key={e.event_id}>
              <td className="muted">{e.seq}</td>
              <td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.ts)}</td>
              <td>{e.event_type}</td><td className="muted">{e.bot_id}</td>
              <td className="muted click" onClick={() => setTrace(e.trace_id || "")}>{e.trace_id}</td>
              <td><code style={{ fontSize: 11 }}>{JSON.stringify(e.payload).slice(0, 220)}</code></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}

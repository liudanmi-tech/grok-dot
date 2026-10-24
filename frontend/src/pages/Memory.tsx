import { useState } from "react";
import { api, fmtTime, post, STATUS } from "../api";
import { usePoll } from "../hooks";

export default function Memory() {
  const [ns, setNs] = useState("");
  const list = usePoll<any[]>(`/memories${ns ? `?namespace=${encodeURIComponent(ns)}` : ""}`, 5000);
  const [form, setForm] = useState({ namespace: "bot/market-intel", type: "preference", content: "" });
  const [err, setErr] = useState<string | null>(null);

  const run = async (fn: () => Promise<any>) => {
    setErr(null);
    try { await fn(); list.reload(); } catch (e: any) { setErr(e.message); }
  };

  return (
    <>
      <header className="top"><h2>记忆</h2>
        <input placeholder="按命名空间筛选，如 bot/market-intel" value={ns} onChange={(e) => setNs(e.target.value)} />
      </header>
      <div className="card">
        <div className="row">
          <input value={form.namespace} onChange={(e) => setForm({ ...form, namespace: e.target.value })} />
          <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            <option value="preference">偏好</option><option value="method">方法</option><option value="summary">摘要</option>
          </select>
          <input style={{ flex: 1, minWidth: 240 }} placeholder="记忆内容（不能含价格、库存、销量数字和个人信息）"
            value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
          <button className="btn primary" disabled={!form.content}
            onClick={() => run(async () => { await post("/memories", form); setForm({ ...form, content: "" }); })}>新增</button>
        </div>
        {err && <div className="err">{err}</div>}
      </div>
      <div className="card">
        <table>
          <thead><tr><th>命名空间</th><th>类型</th><th>内容</th><th>状态</th><th>版本</th><th>过期 / 复核</th><th></th></tr></thead>
          <tbody>{list.data?.map((m) => (
            <tr key={m.memory_id}>
              <td className="muted">{m.namespace}</td><td>{m.type}</td><td>{m.content}</td>
              <td>{STATUS[m.status] || m.status}</td><td>v{m.version}</td>
              <td className="muted">{fmtTime(m.expires_at || m.review_due_at)}</td>
              <td style={{ whiteSpace: "nowrap" }}>
                {m.status === "proposed" && <button className="btn" onClick={() => run(() => post(`/memories/${m.memory_id}/approve`))}>确认</button>}
                <button className="btn" onClick={() => {
                  const c = prompt("修改内容", m.content); if (c === null) return;
                  const r = prompt("修改原因"); if (!r) return;
                  run(() => api(`/memories/${m.memory_id}`, { method: "PATCH", body: JSON.stringify({ content: c, reason: r }) }));
                }}>修改</button>
                <button className="btn danger" onClick={() => {
                  const r = prompt("删除原因"); if (!r) return;
                  run(() => api(`/memories/${m.memory_id}?reason=${encodeURIComponent(r)}`, { method: "DELETE" }));
                }}>删除</button>
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}

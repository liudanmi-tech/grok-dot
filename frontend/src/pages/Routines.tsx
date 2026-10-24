import { useState } from "react";
import { fmtTime, post, STATUS } from "../api";
import { usePoll } from "../hooks";

export default function Routines({ openTask }: { openTask: (id: string) => void }) {
  const bots = usePoll<any[]>("/bots", 0);
  const runs = usePoll<any[]>("/routines/runs", 3000);
  const [err, setErr] = useState<string | null>(null);

  const trigger = async (bot: string, routine: string) => {
    setErr(null);
    try { const t = await post(`/routines/${bot}/${routine}/run`); runs.reload(); openTask(t.task_id); }
    catch (e: any) { setErr(e.message); }
  };

  return (
    <>
      <header className="top"><h2>例程</h2></header>
      <div className="card">
        <table>
          <thead><tr><th>岗位</th><th>例程</th><th>时间（拉各斯）</th><th>补跑时限</th><th></th></tr></thead>
          <tbody>{bots.data?.flatMap((b) => b.triggers.map((t: any) => (
            <tr key={b.id + t.routine}>
              <td>{b.display_name}</td><td>{t.routine}</td><td className="muted">{t.expr}</td>
              <td className="muted">{t.compensate_before}</td>
              <td><button className="btn" onClick={() => trigger(b.id, t.routine)}>手动触发</button></td>
            </tr>
          )))}</tbody>
        </table>
        {err && <div className="err">{err}</div>}
      </div>
      <h3>运行记录</h3>
      <div className="card">
        <table>
          <thead><tr><th>岗位</th><th>例程</th><th>计划时间</th><th>状态</th><th>任务状态</th><th>补偿</th><th>说明</th></tr></thead>
          <tbody>{runs.data?.map((r) => (
            <tr key={r.run_id} className={r.task_id ? "click" : ""} onClick={() => r.task_id && openTask(r.task_id)}>
              <td>{r.bot_id}</td><td>{r.routine}</td><td className="muted">{fmtTime(r.scheduled_at)}</td>
              <td>{STATUS[r.state] || r.state}</td><td>{STATUS[r.task_status] || r.task_status || "—"}</td>
              <td>{r.compensated ? "补跑" : ""}</td><td className="muted">{r.note}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}

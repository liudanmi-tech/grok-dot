import { usePoll } from "../hooks";
import { fmtTime } from "../api";

export default function Overview({ openTask }: { openTask: (id: string) => void }) {
  const bots = usePoll<any[]>("/bots", 5000);
  const usage = usePoll<any>("/usage", 5000);
  const alerts = usePoll<any[]>("/alerts?limit=20", 5000);
  const verify = usePoll<any>("/audit/verify", 10000);
  const sources = usePoll<any[]>("/sources/health", 30000);
  const tasks = usePoll<any[]>("/tasks", 5000);

  return (
    <>
      <header className="top"><h2>总览</h2></header>
      <div className="grid">
        <div className="card"><div className="muted">本月模型费用</div>
          <div className="kpi">${(usage.data?.spent ?? 0).toFixed(3)}</div>
          <div className="muted">全局上限 ${usage.data?.global_budget ?? "—"}</div></div>
        <div className="card"><div className="muted">审计哈希链</div>
          <div className="kpi">{verify.data ? (verify.data.ok ? "完整" : "断链") : "—"}</div>
          <div className="muted">已校验 {verify.data?.checked ?? 0} 条</div></div>
        <div className="card"><div className="muted">进行中的任务</div>
          <div className="kpi">{tasks.data?.filter((t) => !["accepted", "cancelled", "failed", "delivered"].includes(t.status)).length ?? 0}</div>
          <div className="muted">共 {tasks.data?.length ?? 0} 个任务</div></div>
      </div>

      <h3>岗位登记表</h3>
      <div className="card">
        <table>
          <thead><tr><th>岗位</th><th>状态</th><th>作用域</th><th>工具数</th><th>进行中任务</th><th>本月费用 / 预算</th><th>负责人</th></tr></thead>
          <tbody>
            {bots.data?.map((b) => (
              <tr key={b.id}>
                <td><b>{b.display_name}</b><div className="muted">{b.id}</div></td>
                <td><span className={`pill ${b.status === "production" ? "good" : "warn"}`}>{b.status}</span></td>
                <td className="muted">{JSON.stringify(b.scope)}</td>
                <td>{b.tools.length}</td>
                <td>{b.open_tasks}</td>
                <td>${b.month_spend} / ${b.budget ?? "—"}</td>
                <td className="muted">{b.owner}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3>竞品数据源健康度</h3>
      <div className="card">
        <table>
          <thead><tr><th>来源</th><th>状态</th><th>最近成功</th></tr></thead>
          <tbody>{sources.data?.map((s) => (
            <tr key={s.source}><td>{s.name}</td>
              <td>{s.ok ? <span className="pill good">正常</span> : <span className="pill bad">{s.reason}</span>}</td>
              <td className="muted">{fmtTime(s.last_success)}</td></tr>
          ))}</tbody>
        </table>
      </div>

      <h3>最近告警</h3>
      <div className="card">
        {!alerts.data?.length && <div className="muted">暂无告警</div>}
        <table><tbody>
          {alerts.data?.map((a) => (
            <tr key={a.event_id} className={a.payload.task_id ? "click" : ""}
              onClick={() => a.payload.task_id && openTask(a.payload.task_id)}>
              <td style={{ width: 60 }}><span className={`pill ${a.payload.level === "P1" ? "bad" : a.payload.level === "P2" ? "warn" : ""}`}>{a.payload.level}</span></td>
              <td>{a.payload.kind}</td><td className="muted">{a.bot_id}</td><td className="muted">{fmtTime(a.ts)}</td>
            </tr>
          ))}
        </tbody></table>
      </div>
    </>
  );
}

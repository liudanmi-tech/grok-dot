import { fmtTime, STATUS } from "../api";
import { usePoll } from "../hooks";

export default function Messages() {
  const { data } = usePoll<any[]>("/outbox", 3000);
  return (
    <>
      <header className="top"><h2>消息卡片</h2><span className="muted">飞书 / 企业微信出站卡片（POC 在此查看；进度卡片原地更新）</span></header>
      {data?.map((m) => (
        <div className="card" key={m.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>{m.card.title}</b>
            <span className="row"><span className="pill info">{m.kind}</span><span className="muted">→ {m.recipient} · {fmtTime(m.updated_at)}</span></span>
          </div>
          {m.kind === "task_progress" && (
            <div className="muted">{STATUS[m.card.status]} · 进度 {m.card.progress} · 费用 {m.card.cost}
              {m.card.last_note ? ` · ${m.card.last_note}` : ""}
              {m.card.buttons?.length ? ` · 按钮：${m.card.buttons.join(" / ")}` : ""}</div>
          )}
          {m.kind === "approval" && (
            <div className="muted">{STATUS[m.card.state]} · 预审 {m.card.review_level} · 过期 {fmtTime(m.card.expires_at)}</div>
          )}
          {m.card.body && <pre>{m.card.body}</pre>}
        </div>
      ))}
    </>
  );
}

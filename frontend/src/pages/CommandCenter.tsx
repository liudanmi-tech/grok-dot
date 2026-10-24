import { useEffect, useMemo, useState } from "react";
import { ArrowUp, Bot, CheckSquare, CircleDollarSign, Inbox, Plus, Users, Zap } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { ActivityPill, Avatar, describe, timeAgo } from "../ui";

export const SAMPLES: Record<string, string[]> = {
  "market-intel": ["对比 Samsung 和 Redmi 本周在主流价位段的调价动作", "汇总近 7 天竞品新品及对我方影响"],
  "biz-review": ["拉各斯 TECNO 上周销量变化最大的型号是哪些？", "各区域本周动销对比，找出下滑最快的区域"],
  "price-sentinel": ["扫描各区域动销下滑的型号并分析原因", "Spark X6851 在拉各斯是否需要促销？"],
  "content-factory": ["为 Spark X6851 写一组周末促销文案（英语、皮钦语、豪萨语）", "为 Infinix Hot 50 写 3 条 WhatsApp 状态文案"],
  "voice-of-customer": ["近一个月哪个型号的售后问题最多？原因可能是什么？", "Infinix Hot 50 充电问题的趋势和建议"],
  "promoter-coach": ["怎么向顾客介绍 Infinix Hot 50 的卖点？", "顾客拿 Redmi Note 14 比价时怎么回应？"],
};

function greeting() {
  const h = new Date().getHours();
  return h < 11 ? "早上好" : h < 14 ? "中午好" : h < 18 ? "下午好" : "晚上好";
}

export default function CommandCenter({ me, team, ov, reload, go }: any) {
  const feed = usePoll<any[]>("/activity?limit=40", 3000);
  const dispatchable = team.filter((b: any) => b.state === "active" && b.channel !== "WhatsApp");
  const [target, setTarget] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!target && dispatchable[0]) setTarget(dispatchable[0].id); }, [dispatchable, target]);
  const names = useMemo(() => Object.fromEntries(team.map((b: any) => [b.id, b.display_name])), [team]);
  const byId = useMemo(() => Object.fromEntries(team.map((b: any) => [b.id, b])), [team]);
  const sel = byId[target || ""];
  const working = team.filter((b: any) => b.activity === "working").length;

  const send = async () => {
    if (!target || !text.trim()) return;
    setErr(null); setBusy(true);
    try { await post("/tasks", { bot_id: target, goal: text.trim() }); setText(""); reload(); go(`bot/${target}`); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{greeting()}，{me?.name?.split("·")[0]?.trim() || "负责人"}</h1>
          <p>你的团队有 <b>{ov?.active ?? "—"}</b> 名智能体在岗，<b>{working}</b> 名正在工作，<b>{ov?.inbox_count ?? 0}</b> 件事等你决定。</p>
        </div>
        <button className="btn" onClick={() => go("library")}><Plus size={14} />招募成员</button>
      </div>

      <div className="grid g4" style={{ marginBottom: 16 }}>
        <div className="card kpi"><div className="l"><Users size={14} />在岗成员</div><div className="v">{ov?.active ?? "—"}<span className="muted"> / {ov?.team_size ?? "—"}</span></div></div>
        <div className="card kpi"><div className="l"><Zap size={14} />今日任务</div><div className="v">{ov?.done_today ?? 0}<span className="muted"> 完成 / {ov?.tasks_today ?? 0}</span></div></div>
        <div className="card kpi"><div className="l"><Inbox size={14} />等你决策</div><div className="v" style={{ color: ov?.inbox_count ? "var(--warn)" : undefined }}>{ov?.inbox_count ?? 0}</div></div>
        <div className="card kpi"><div className="l"><CircleDollarSign size={14} />本月模型费用</div><div className="v">${(ov?.spend_month ?? 0).toFixed(2)}<span className="muted"> / ${ov?.budget_month ?? "—"}</span></div></div>
      </div>

      <div className="composer" style={{ marginBottom: 18 }}>
        <div className="row" style={{ marginBottom: 6 }}>
          <span className="muted small">派给</span>
          {dispatchable.map((b: any) => (
            <span key={b.id} className={`mention ${target === b.id ? "on" : ""}`} onClick={() => setTarget(b.id)}>
              <Avatar bot={b} size="sm" dot={false} />@{b.display_name}
            </span>
          ))}
        </div>
        <textarea rows={2} value={text} placeholder={sel ? `告诉 ${sel.display_name} 要做什么…（Enter 发送，Shift+Enter 换行）` : "先招募一名成员"}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <div className="row between">
          <div className="row">{(SAMPLES[sel?.template] || []).map((s) => (
            <button key={s} className="btn sm" onClick={() => setText(s)}>{s.length > 24 ? s.slice(0, 24) + "…" : s}</button>
          ))}</div>
          <div className="row">
            <span className="muted small">数据范围 = 成员权限 ∩ 你的权限 · 大任务会先给你看计划</span>
            <button className="btn primary" disabled={!text.trim() || !target || busy} onClick={send}><ArrowUp size={14} />派活</button>
          </div>
        </div>
        {err && <div className="err">{err}</div>}
      </div>

      <div className="split">
        <div>
          <div className="card-head"><h2>团队</h2><span className="muted small">点击成员查看工作流与档案</span></div>
          <div className="grid g3">
            {team.map((b: any) => (
              <div key={b.id} className="card agent" onClick={() => go(`bot/${b.id}`)}>
                <div className="row" style={{ flexWrap: "nowrap" }}>
                  <Avatar bot={b} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{b.display_name}</div>
                    <div className="muted small">{b.title}</div>
                  </div>
                </div>
                <div className="row between"><ActivityPill a={b.activity} />{b.channel !== "内部" && <span className="tag">{b.channel}</span>}</div>
                <div className="now">
                  {b.current ? (
                    <>
                      <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{b.current.goal}</div>
                      <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${b.current.progress_total ? (100 * b.current.progress_done) / b.current.progress_total : 5}%` }} /></div>
                    </>
                  ) : <span className="muted">{b.activity === "paused" ? "已暂停" : "空闲，等待派活"}</span>}
                </div>
                <div className="row between muted small">
                  <span>今日完成 {b.done_today}</span>
                  <span>本月 ${b.month_spend.toFixed(3)}{b.budget ? ` / $${b.budget}` : ""}</span>
                </div>
              </div>
            ))}
            <div className="card agent" style={{ borderStyle: "dashed", alignItems: "center", justifyContent: "center", color: "var(--quiet)", minHeight: 170 }}
              onClick={() => go("library")}>
              <Bot size={22} /><div>从智能体库招募新成员</div>
            </div>
          </div>
        </div>

        <div>
          <div className="card">
            <div className="card-head"><h2><Inbox size={15} /> 等你决策</h2><span className="pill warn">{ov?.inbox_count ?? 0}</span></div>
            {!ov?.inbox_count && <div className="muted">全部处理完了。</div>}
            {ov?.inbox?.approvals?.map((a: any) => (
              <div className="inbox-item" key={a.approval_id}>
                <Avatar bot={byId[a.bot_id]} size="sm" dot={false} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div><b>审批</b> · {a.action_class}</div>
                  <div className="muted small">{names[a.bot_id] || a.bot_id} · {String(a.target)} · 预审{a.review === "low" ? "低风险" : a.review === "attention" ? "需关注" : ""} · {timeAgo(a.created_at)}</div>
                </div>
                <button className="btn sm" onClick={() => go(a.bot_id === "dealer-assistant" ? "whatsapp" : "approvals")}><CheckSquare size={12} />处理</button>
              </div>
            ))}
            {[...(ov?.inbox?.confirm || []).map((t: any) => ({ ...t, k: "确认计划" })),
              ...(ov?.inbox?.human || []).map((t: any) => ({ ...t, k: "需要人工" })),
              ...(ov?.inbox?.review || []).map((t: any) => ({ ...t, k: "验收交付" }))].map((t: any) => (
              <div className="inbox-item" key={t.k + t.task_id}>
                <Avatar bot={byId[t.bot_id]} size="sm" dot={false} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div><b>{t.k}</b></div>
                  <div className="muted small" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{names[t.bot_id] || t.bot_id} · {t.goal}</div>
                </div>
                <button className="btn sm" onClick={() => go(`bot/${t.bot_id}`)}>查看</button>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-head"><h2>团队动态</h2><span className="muted small">实时 · 来自审计日志</span></div>
            <div className="feed">
              {feed.data?.map((e: any, i: number) => (
                <div className="feed-item" key={i}>
                  {e.bot_id ? <Avatar bot={byId[e.bot_id] || { display_name: e.bot_name }} size="sm" dot={false} /> : <div className="av sm" style={{ background: "#475569" }}>系</div>}
                  <div style={{ minWidth: 0 }}>
                    <div><b>{e.bot_name || "系统"}</b> {describe(e, names)}</div>
                    <div className="t">{timeAgo(e.ts)}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

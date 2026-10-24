import { useEffect, useState } from "react";
import { Check, Circle, Loader2, Minus, X } from "lucide-react";
import { STATUS } from "./api";

// ---------- 路由：#/home、#/bot/<id>、#/library、#/tasks/<id> ... ----------
export function useRoute(): [string[], (path: string) => void] {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "home").split("/");
  const [parts, setParts] = useState(read());
  useEffect(() => {
    const on = () => setParts(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [parts, (path: string) => { window.location.hash = "/" + path; }];
}

// ---------- 头像与状态 ----------
export function initials(name?: string) {
  if (!name) return "AI";
  const base = name.split("·")[0].trim();
  return /[一-鿿]/.test(base) ? base.slice(0, 2) : base.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

export function Avatar({ bot, size, dot = true }: { bot: any; size?: "sm" | "lg"; dot?: boolean }) {
  return (
    <div className={`av ${size || ""}`} style={{ background: `linear-gradient(135deg, ${bot?.color || "#64748b"}, ${shade(bot?.color || "#64748b")})` }}>
      {initials(bot?.display_name)}
      {dot && bot?.activity && <span className={`dot ${bot.activity}`} />}
    </div>
  );
}

function shade(hex: string) {
  const n = parseInt(hex.replace("#", ""), 16);
  const r = Math.max(0, (n >> 16) - 40), g = Math.max(0, ((n >> 8) & 255) - 40), b = Math.max(0, (n & 255) - 40);
  return `rgb(${r},${g},${b})`;
}

export const ACTIVITY: Record<string, [string, string]> = {
  working: ["info", "干活中"], needs_you: ["warn", "等你处理"], idle: ["good", "待命"], off_duty: ["off", "下班"],
  paused: ["", "已暂停"], retired: ["", "已离岗"],
};

export function ActivityPill({ a }: { a: string }) {
  const [cls, text] = ACTIVITY[a] || ["", a];
  return <span className={`pill ${cls}`}><span className={`dot ${a}`} />{text}</span>;
}

export function TaskPill({ status }: { status: string }) {
  const cls = ["delivered", "accepted"].includes(status) ? "good"
    : ["failed", "cancelled"].includes(status) ? "bad"
    : ["awaiting_confirmation", "needs_human", "paused", "awaiting_approval", "rework"].includes(status) ? "warn" : "info";
  return <span className={`pill ${cls}`}>{STATUS[status] || status}</span>;
}

export function StepList({ steps }: { steps: any[] }) {
  const icon = (s: string) => s === "done" ? <Check size={14} /> : s === "running" ? <Loader2 size={14} className="spin" />
    : s === "failed" ? <X size={14} /> : s === "skipped" ? <Minus size={14} /> : <Circle size={10} />;
  return (
    <ul className="steps">
      {steps.map((s: any) => (
        <li key={s.index} className={s.status}>
          <span className="ic">{icon(s.status)}</span>
          <span>{s.title}</span>
          {s.tool && <span className="muted small mono">{s.tool}</span>}
          {s.note && <span className="muted small">· {s.note}</span>}
        </li>
      ))}
    </ul>
  );
}

export function timeAgo(s?: string | null) {
  if (!s) return "";
  const d = (Date.now() - new Date(s).getTime()) / 1000;
  if (d < 60) return "刚刚";
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  return `${Math.floor(d / 86400)} 天前`;
}

// 审计事件 → 一句话
export function describe(e: any, names: Record<string, string>) {
  const d = e.data || {};
  const who = (id?: string) => names[id || ""] || id || "";
  switch (e.type) {
    case "task.create": return d.source === "coordinator" ? `接到总调度分派：${d.goal || ""}`
      : `接到任务${d.source === "routine" ? "（例程）" : d.source === "whatsapp" ? "（WhatsApp）" : ""}：${d.goal || ""}`;
    case "task.plan": return d.by === "coordinator" ? `拆成 ${d.subtasks} 项分工，派给团队并行执行` : `拆出 ${d.steps} 步计划${d.large ? "，等待下达人确认" : ""}`;
    case "task.status": return `任务${STATUS[d.from_status] || d.from_status} → ${STATUS[d.to_status] || d.to_status}${d.note ? "：" + d.note : ""}`;
    case "tool.call": return d.decision === "approval" ? `提交审批：${d.tool}` : `调用 ${d.tool}`;
    case "approval.create": return `提交审批（${d.action_class}，预审${d.review_level === "low" ? "低风险" : d.review_level === "attention" ? "需关注" : "退回"}）`;
    case "approval.decide": return `审批被${d.decision === "approve" ? "批准" : d.decision === "edit" ? "修改后批准" : "拒绝"}（${d.actor}）`;
    case "bus.send": return `把产出转交给 ${who(d.to_bot)}`;
    case "model.fallback": return `模型降级 ${d.from_model} → ${d.to_model}`;
    case "bot.hire": return `加入团队（由 ${d.actor} 招募）`;
    case "bot.update": return d.state ? `状态改为 ${d.state}` : "资料已更新";
    case "message.inbound": return "收到经销商 WhatsApp 消息";
    case "message.outbound": return "向经销商发出回复";
    case "memory.write": return "记下一条经验";
    case "alert": return `告警 ${d.level}：${d.kind}`;
    default: return e.type;
  }
}

// ---------- 轻量 Markdown（模型产出用；不解析 HTML，避免注入） ----------
function inline(text: string, key: string | number): any[] {
  const out: any[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|【[^】]{1,12}】)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<b key={`${key}-${i++}`}>{t.slice(2, -2)}</b>);
    else if (t.startsWith("`")) out.push(<code key={`${key}-${i++}`}>{t.slice(1, -1)}</code>);
    else out.push(<span key={`${key}-${i++}`} className="mark">{t}</span>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const lines = (text || "").replace(/\r/g, "").split("\n");
  const blocks: any[] = [];
  let i = 0;
  const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    const h = /^(#{1,4})\s+(.*)/.exec(l);
    if (h) { blocks.push(<div key={i} className={`md-h md-h${h[1].length}`}>{inline(h[2], i)}</div>); i++; continue; }
    if (/^\s*\|/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(l); const rows: string[][] = []; i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]));
      blocks.push(<div key={i} className="md-table"><table><thead><tr>{head.map((c, k) => <th key={k}>{inline(c, k)}</th>)}</tr></thead>
        <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, k) => <td key={k}>{inline(c, `${ri}-${k}`)}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d/.test(l); const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={i}>{items.map((t, k) => <li key={k}>{inline(t, k)}</li>)}</L>);
      continue;
    }
    if (/^-{3,}\s*$/.test(l)) { blocks.push(<hr key={i} />); i++; continue; }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|\s*\||\s*([-*•]|\d+[.)])\s+|-{3,}\s*$)/.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    blocks.push(<p key={i}>{inline(para.join(" "), i)}</p>);
  }
  return <div className="md">{blocks}</div>;
}

// ---------- 交付卡片：服务端渲染的独立 HTML，沙箱 iframe 嵌入（无脚本），高度随内容 ----------
export function DeliverableCard({ taskId, index, fallback, fold = true, onAction }: { taskId: string; index: number; fallback?: string; fold?: boolean;
  onAction?: (a: { text: string; owner: string; due: string }) => void }) {
  const [doc, setDoc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [h, setH] = useState(240);
  const [full, setFull] = useState(!fold);
  const FOLD = 640;
  useEffect(() => {
    let live = true;
    fetch(`/api/tasks/${taskId}/outputs/${index}/html`, { headers: { "X-User-Id": currentUserId() } })
      .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
      .then((t) => live && setDoc(t)).catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [taskId, index]);
  const fit = (el: HTMLIFrameElement | null) => {
    const d = el?.contentDocument;
    if (!d?.body) return;
    const measure = () => setH(Math.max(120, d.documentElement.scrollHeight + 2));
    if (onAction) {  // 卡片无脚本；由外层页面挂点击：「下一步」→ 在对话里继续推进
      d.body.classList.add("interactive");
      d.querySelectorAll<HTMLElement>("[data-act]").forEach((el) => {
        el.onclick = () => onAction({ text: el.dataset.text || "", owner: el.dataset.owner || "", due: el.dataset.due || "" });
      });
    }
    measure();
    new ResizeObserver(measure).observe(d.body);
  };
  const open = (download: boolean) => {
    if (!doc) return;
    const url = URL.createObjectURL(new Blob([doc], { type: "text/html;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.target = "_blank"; a.rel = "noopener";
    if (download) a.download = `交付-${taskId}-${index + 1}.html`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  };
  if (failed) return fallback ? <div className="output rich"><Markdown text={fallback} /></div> : null;
  return (
    <div className="deliverable">
      <div className={`deliverable-frame ${!full && h > FOLD ? "folded" : ""}`} style={{ maxHeight: full ? undefined : FOLD }}>
        {doc ? <iframe title="交付卡片" sandbox="allow-same-origin" srcDoc={doc} style={{ height: h }} onLoad={(e) => fit(e.currentTarget)} />
          : <div className="muted small" style={{ padding: 12 }}>加载交付卡片…</div>}
      </div>
      <div className="deliverable-bar">
        {h > FOLD && <button className="btn ghost sm" onClick={() => setFull(!full)}>{full ? "收起" : "展开全文"}</button>}
        <button className="btn ghost sm" onClick={() => open(false)}>新窗口打开</button>
        <button className="btn ghost sm" onClick={() => open(true)}>下载 HTML</button>
      </div>
    </div>
  );
}

function currentUserId() {
  try { return localStorage.getItem("ngap.user") || "u-country-manager"; } catch { return "u-country-manager"; }
}

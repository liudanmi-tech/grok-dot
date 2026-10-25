import { useEffect, useState } from "react";
import { Activity, BookOpen, Bot, CheckSquare, ChevronDown, ChevronRight, Clock, Cpu, Gauge, Inbox, ListTodo, MessageCircle,
  MessagesSquare, Plus, Library as LibraryIcon, Sparkles, ClipboardList, ScrollText, Shield, Target } from "lucide-react";
import { api, currentUser, setCurrentUser } from "./api";
import { usePoll } from "./hooks";
import { useRoute } from "./ui";
import Goals from "./pages/Goals";
import DemoClock from "./pages/DemoClock";
import Member from "./pages/Member";
import Library from "./pages/Library";
import Tasks from "./pages/Tasks";
import Approvals from "./pages/Approvals";
import Messages from "./pages/Messages";
import Memory from "./pages/Memory";
import Audit from "./pages/Audit";
import Routines from "./pages/Routines";
import WhatsApp from "./pages/WhatsApp";
import Settings from "./pages/Settings";
import Chats from "./pages/Chats";
import Chat from "./pages/Chat";
import Team from "./pages/Team";
import SkillPlaza from "./pages/SkillPlaza";
import Knowledge from "./pages/Knowledge";
import FieldReports from "./pages/FieldReports";

export default function App() {
  const [route, go] = useRoute();
  const [user, setUser] = useState(currentUser());
  const [users, setUsers] = useState<any[]>([]);
  const team = usePoll<any[]>(`/bots?u=${user}`, 3000);
  const ov = usePoll<any>(`/overview?u=${user}`, 4000);
  const kpis = usePoll<any[]>("/kpis", 5000);
  const convs = usePoll<any[]>("/conversations", 5000);
  const [more, setMore] = useState(false);

  useEffect(() => { api("/users").then(setUsers).catch(() => {}); }, []);
  const me = users.find((u) => u.id === user);
  // 目标驾驶舱与 KPI 合并为「目标」：旧链接 #/cockpit、#/kpi/<id>、#/home 自动归并
  const page = ["home", "cockpit", "kpi", "goals"].includes(route[0]) ? "goals" : route[0];
  const goalId = route[1] && route[1] !== "new" ? route[1] : undefined;
  const lagging = (kpis.data || []).flatMap((k: any) => [k, ...(k.children || [])]).filter((k: any) => k.progress.rag === "red").length;
  const switchUser = (id: string) => { setCurrentUser(id); setUser(id); };
  const Nav = ({ to, icon, label, count }: { to: string; icon: any; label: string; count?: number }) => (
    <button className={`nav ${page === to ? "on" : ""}`} onClick={() => go(to)}>
      {icon}<span>{label}</span>{count ? <span className="count">{count}</span> : null}
    </button>
  );

  const recent = (convs.data || []).slice(0, 5);
  const team6 = sortTeam(team.data);
  return (
    <div className="layout">
      <aside className="side">
        <div className="brand">
          <div className="brand-mark">AO</div>
          <div><b>Agent Org</b><span>尼日利亚销售 · 虚拟组织</span></div>
        </div>
        <button className={`nav ${page === "goals" ? "on" : ""}`} onClick={() => go("goals")}>
          <Gauge size={16} /><span>目标</span>
          {lagging ? <span className="count" title="落后的 KPI">{lagging}</span> : <span className="sub">{(kpis.data || []).length} 个 KPI</span>}
        </button>

        <div className="side-label"><span>任务对话</span>
          <button className="btn ghost sm" style={{ color: "var(--side-ink)", padding: 2 }} title="新建对话" onClick={() => go("chats")}><Plus size={14} /></button>
        </div>
        <Nav to="chats" icon={<MessagesSquare size={16} />} label="全部对话" count={(convs.data || []).filter((c: any) => c.attention).length} />
        {recent.map((c: any) => (
          <button key={c.task_id} className={`nav sub-nav ${page === "chat" && route[1] === c.task_id ? "on" : ""}`} onClick={() => go(`chat/${c.task_id}`)}>
            <span className={`conv-dot ${c.attention ? "attn" : ["delivered", "accepted"].includes(c.status) ? "done" : ["failed", "cancelled"].includes(c.status) ? "" : "run"}`} />
            <span className="ellipsis">{c.goal}</span>
          </button>
        ))}

        <div className="side-label"><span>组织</span></div>
        <Nav to="team" icon={<Bot size={16} />} label={`数字员工 · ${team6.length}`} count={team6.filter((b: any) => b.activity === "needs_you").length} />
        <Nav to="field" icon={<ClipboardList size={16} />} label="一线日报" />
        <Nav to="skills" icon={<Sparkles size={16} />} label="技能广场" />
        <Nav to="kb" icon={<LibraryIcon size={16} />} label="知识库" />
        <Nav to="approvals" icon={<CheckSquare size={16} />} label="审批" count={ov.data?.inbox?.approvals?.length} />

        <button className="side-label side-toggle" onClick={() => setMore(!more)}>
          <span>治理与渠道</span>{more ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        {more && (
          <>
            <Nav to="whatsapp" icon={<MessageCircle size={16} />} label="经销商渠道" />
            <Nav to="routines" icon={<Clock size={16} />} label="例程" />
            <Nav to="tasks" icon={<ListTodo size={16} />} label="任务台账" />
            <Nav to="memory" icon={<BookOpen size={16} />} label="记忆" />
            <Nav to="audit" icon={<ScrollText size={16} />} label="审计" />
            <Nav to="messages" icon={<Inbox size={16} />} label="消息卡片" />
            <Nav to="settings" icon={<Cpu size={16} />} label="模型设置" />
          </>
        )}

        <div className="side-foot">
          <div className="row" style={{ marginBottom: 8, color: "#fff" }}>
            <div className="av sm" style={{ background: "#334155" }}>{(me?.name || "我").slice(0, 1)}</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.3 }}>{me?.name || "—"}<div style={{ color: "var(--side-quiet)", fontSize: 11 }}>
              <Shield size={10} /> {me?.roles?.filter((r: string) => r !== "requester").join(" · ") || "员工"}</div></div>
          </div>
          <select value={user} onChange={(e) => switchUser(e.target.value)}>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <div style={{ color: "var(--side-quiet)", fontSize: 11, marginTop: 6 }}><Activity size={10} /> POC 模拟登录 · 审计链 {ov.data?.audit_ok ? "完整" : "…"}</div>
        </div>
      </aside>

      <main key={user} className={page === "chat" ? "wide" : ""}>
        <DemoClock onChange={() => { kpis.reload(); team.reload(); ov.reload(); convs.reload(); }} />
        {page === "goals" && <Goals id={goalId} me={me} team={team6} go={go} onChange={() => { kpis.reload(); team.reload(); }} switchUser={switchUser} autoNew={route[1] === "new"} />}
        {page === "chats" && <Chats team={team6} go={go} />}
        {page === "chat" && route[1] && <Chat id={route[1]} team={team6} users={users} go={go} />}
        {page === "team" && <Team go={go} />}
        {page === "skills" && <SkillPlaza me={me} />}
        {page === "field" && <FieldReports me={me} />}
        {page === "kb" && <Knowledge me={me} />}
        {page === "bot" && route[1] && <Member id={route[1]} go={go} onChange={() => team.reload()} team={team.data || []} />}
        {page === "library" && <Library go={go} onHired={() => team.reload()} />}
        {page === "tasks" && <Tasks focus={route[1] || null} setFocus={(id) => go(id ? `tasks/${id}` : "tasks")} />}
        {page === "approvals" && <Approvals />}
        {page === "whatsapp" && <WhatsApp />}
        {page === "messages" && <Messages />}
        {page === "routines" && <Routines openTask={(id) => go(`tasks/${id}`)} />}
        {page === "memory" && <Memory />}
        {page === "audit" && <Audit />}
        {page === "settings" && <Settings />}
      </main>
    </div>
  );
}

// 总调度排在团队最前（团队负责人）
export function sortTeam(list?: any[] | null) {
  return [...(list || [])].sort((a, b) => Number(b.template === "chief-of-staff") - Number(a.template === "chief-of-staff"));
}

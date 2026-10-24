import { useEffect, useState } from "react";
import { Activity, BookOpen, CheckSquare, Clock, Cpu, Gauge, Inbox, LayoutDashboard, ListTodo, MessageCircle, Plus, Target,
  ScrollText, Shield, Sparkles } from "lucide-react";
import { api, currentUser, setCurrentUser } from "./api";
import { usePoll } from "./hooks";
import { Avatar, useRoute } from "./ui";
import CommandCenter from "./pages/CommandCenter";
import Cockpit from "./pages/Cockpit";
import Kpi from "./pages/Kpi";
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

export default function App() {
  const [route, go] = useRoute();
  const [user, setUser] = useState(currentUser());
  const [users, setUsers] = useState<any[]>([]);
  const team = usePoll<any[]>(`/bots?u=${user}`, 3000);
  const ov = usePoll<any>(`/overview?u=${user}`, 4000);
  const kpis = usePoll<any[]>("/kpis", 5000);

  useEffect(() => { api("/users").then(setUsers).catch(() => {}); }, []);
  const me = users.find((u) => u.id === user);
  const page = route[0];
  const Nav = ({ to, icon, label, count }: { to: string; icon: any; label: string; count?: number }) => (
    <button className={`nav ${page === to ? "on" : ""}`} onClick={() => go(to)}>
      {icon}<span>{label}</span>{count ? <span className="count">{count}</span> : null}
    </button>
  );

  return (
    <div className="layout">
      <aside className="side">
        <div className="brand">
          <div className="brand-mark">AO</div>
          <div><b>Agent Org</b><span>尼日利亚销售 · 虚拟组织</span></div>
        </div>
        <Nav to="home" icon={<LayoutDashboard size={16} />} label="指挥中心" count={ov.data?.inbox_count} />
        <Nav to="cockpit" icon={<Gauge size={16} />} label="目标驾驶舱" />
        {(kpis.data || []).flatMap((k: any) => [k, ...(k.children || [])]).map((k: any) => (
          <button key={k.kpi_id} className={`nav sub-nav ${page === "kpi" && route[1] === k.kpi_id ? "on" : ""}`} onClick={() => go(`kpi/${k.kpi_id}`)}>
            <Target size={13} /><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{k.title}</span>
            <span className={`rag-dot ${k.progress.rag || ""}`} />
          </button>
        ))}

        <div className="side-label"><span>我的团队 · {team.data?.length ?? 0}</span>
          <button className="btn ghost sm" style={{ color: "var(--side-ink)", padding: 2 }} title="招募成员" onClick={() => go("library")}><Plus size={14} /></button>
        </div>
        {sortTeam(team.data).map((b) => (
          <button key={b.id} className={`nav ${page === "bot" && route[1] === b.id ? "on" : ""}`} onClick={() => go(`bot/${b.id}`)}>
            <Avatar bot={b} size="sm" />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{b.display_name}</span>
            {b.template === "chief-of-staff" && !(b.attention + b.approvals) && b.activity !== "working" ? <span className="sub">负责人</span>
              : b.attention + b.approvals > 0 ? <span className="count">{b.attention + b.approvals}</span>
              : b.activity === "working" ? <span className="sub">干活中</span>
              : b.activity === "off_duty" ? <span className="sub">下班</span> : null}
          </button>
        ))}
        <Nav to="library" icon={<Sparkles size={16} />} label="招募成员" />

        <div className="side-label"><span>工作</span></div>
        <Nav to="tasks" icon={<ListTodo size={16} />} label="任务" />
        <Nav to="approvals" icon={<CheckSquare size={16} />} label="审批" count={ov.data?.inbox?.approvals?.length} />
        <Nav to="whatsapp" icon={<MessageCircle size={16} />} label="经销商渠道" />
        <Nav to="messages" icon={<Inbox size={16} />} label="消息卡片" />

        <div className="side-label"><span>治理</span></div>
        <Nav to="routines" icon={<Clock size={16} />} label="例程" />
        <Nav to="memory" icon={<BookOpen size={16} />} label="记忆" />
        <Nav to="audit" icon={<ScrollText size={16} />} label="审计" />
        <Nav to="settings" icon={<Cpu size={16} />} label="模型设置" />

        <div className="side-foot">
          <div className="row" style={{ marginBottom: 8, color: "#fff" }}>
            <div className="av sm" style={{ background: "#334155" }}>{(me?.name || "我").slice(0, 1)}</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.3 }}>{me?.name || "—"}<div style={{ color: "var(--side-quiet)", fontSize: 11 }}>
              <Shield size={10} /> {me?.roles?.filter((r: string) => r !== "requester").join(" · ") || "员工"}</div></div>
          </div>
          <select value={user} onChange={(e) => { setCurrentUser(e.target.value); setUser(e.target.value); }}>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <div style={{ color: "var(--side-quiet)", fontSize: 11, marginTop: 6 }}><Activity size={10} /> POC 模拟登录 · 审计链 {ov.data?.audit_ok ? "完整" : "…"}</div>
        </div>
      </aside>

      <main key={user}>
        <DemoClock onChange={() => { kpis.reload(); team.reload(); ov.reload(); }} />
        {page === "home" && <CommandCenter me={me} team={sortTeam(team.data)} ov={ov.data} reload={() => { team.reload(); ov.reload(); }} go={go} />}
        {page === "bot" && route[1] && <Member id={route[1]} go={go} onChange={() => team.reload()} team={team.data || []} />}
        {page === "cockpit" && <Cockpit me={me} go={go} onChange={() => kpis.reload()} switchUser={(id: string) => { setCurrentUser(id); setUser(id); }} autoNew={route[1] === "new"} />}
        {page === "kpi" && route[1] && <Kpi id={route[1]} me={me} team={team.data || []} go={go} onChange={() => { kpis.reload(); team.reload(); }} switchUser={(id: string) => { setCurrentUser(id); setUser(id); }} />}
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

import { useEffect, useState } from "react";
import { api, currentUser, setCurrentUser } from "./api";
import { usePoll } from "./hooks";
import Overview from "./pages/Overview";
import Tasks from "./pages/Tasks";
import Approvals from "./pages/Approvals";
import Messages from "./pages/Messages";
import Memory from "./pages/Memory";
import Audit from "./pages/Audit";
import Routines from "./pages/Routines";
import WhatsApp from "./pages/WhatsApp";

const PAGES = [
  ["whatsapp", "WhatsApp"], ["overview", "总览"], ["tasks", "任务"], ["approvals", "审批"], ["messages", "消息卡片"],
  ["routines", "例程"], ["memory", "记忆"], ["audit", "审计"],
] as const;
type Page = (typeof PAGES)[number][0];

export default function App() {
  const [page, setPage] = useState<Page>("whatsapp");
  const [user, setUser] = useState(currentUser());
  const [users, setUsers] = useState<any[]>([]);
  const [focusTask, setFocusTask] = useState<string | null>(null);
  const pending = usePoll<any[]>(`/approvals?state=pending,escalated&mine=true&u=${user}`, 5000);

  useEffect(() => { api("/users").then(setUsers).catch(() => {}); }, []);

  const switchUser = (id: string) => { setCurrentUser(id); setUser(id); };
  const openTask = (id: string) => { setFocusTask(id); setPage("tasks"); };

  return (
    <div className="layout">
      <nav>
        <h1>智能体平台 · 尼日利亚</h1>
        {PAGES.map(([k, label]) => (
          <button key={k} className={page === k ? "on" : ""} onClick={() => setPage(k)}>
            {label}
            {k === "approvals" && pending.data && pending.data.length > 0 && <span className="badge">{pending.data.length}</span>}
          </button>
        ))}
        <div style={{ marginTop: 24 }}>
          <div className="muted">当前用户（POC 模拟登录）</div>
          <select value={user} onChange={(e) => switchUser(e.target.value)} style={{ width: "100%", marginTop: 6 }}>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </div>
      </nav>
      <main key={user}>
        {page === "whatsapp" && <WhatsApp />}
        {page === "overview" && <Overview openTask={openTask} />}
        {page === "tasks" && <Tasks focus={focusTask} setFocus={setFocusTask} />}
        {page === "approvals" && <Approvals />}
        {page === "messages" && <Messages />}
        {page === "routines" && <Routines openTask={openTask} />}
        {page === "memory" && <Memory />}
        {page === "audit" && <Audit />}
      </main>
    </div>
  );
}

import { Award, Sparkles, ThumbsDown, ThumbsUp } from "lucide-react";
import { usePoll } from "../hooks";
import { ActivityPill, Avatar } from "../ui";

export function LevelBadge({ g }: { g: any }) {
  const pct = g.next_xp ? Math.min(100, (100 * g.xp) / g.next_xp) : 100;
  return (
    <div className="lvl">
      <span className="lvl-name"><Award size={12} />{g.level}</span>
      <span className="bar" style={{ flex: 1 }}><i style={{ width: `${pct}%`, background: "var(--warn)" }} /></span>
      <span className="muted small">{g.xp}{g.next_xp ? ` / ${g.next_xp}` : ""} XP</span>
    </div>
  );
}

export default function Team({ go }: any) {
  const { data } = usePoll<any[]>("/employees", 5000);
  const list = [...(data || [])].sort((a, b) => Number(b.template === "chief-of-staff") - Number(a.template === "chief-of-staff"));
  const count = (k: string) => list.filter((b) => b.activity === k).length;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>数字员工</h1>
          <p>共 {list.length} 名 · 干活中 {count("working")} · 待命 {count("idle")} · 下班 {count("off_duty")} · 等你处理 {count("needs_you")}。等级由交付、验收和晚会点评累计。</p>
        </div>
        <button className="btn primary" onClick={() => go("library")}><Sparkles size={14} />从智能体库招募</button>
      </div>
      <div className="grid g3">
        {list.map((b) => {
          const g = b.growth;
          return (
            <div key={b.id} className="card agent" onClick={() => go(`bot/${b.id}`)}>
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <Avatar bot={b} size="lg" />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="row" style={{ flexWrap: "nowrap" }}><b className="ellipsis">{b.display_name}</b><span style={{ marginLeft: "auto" }}><ActivityPill a={b.activity} /></span></div>
                  <div className="muted small">{b.title}{b.scope?.region ? ` · ${b.scope.region.join("/")}` : ""}</div>
                </div>
              </div>
              <LevelBadge g={g} />
              <div className="row">{(b.capabilities || []).slice(0, 4).map((c: string) => <span key={c} className="tag">{c}</span>)}</div>
              <div className="muted small">技能：{b.skills.map((s: string) => s.split("/").pop()).join("、") || "—"}</div>
              <div className="row between small">
                <span>交付 <b>{g.delivered}</b> · 验收 <b>{g.accepted}</b> · 返工 <b>{g.rework}</b></span>
                <span className="row" style={{ gap: 8 }}>
                  <span title="晚会点评：超出预期"><ThumbsUp size={12} /> {g.ratings.good}</span>
                  <span title="晚会点评：符合预期">👌 {g.ratings.ok}</span>
                  <span title="晚会点评：落后"><ThumbsDown size={12} /> {g.ratings.behind}</span>
                </span>
              </div>
              {g.feedback[0] && <div className="muted small ellipsis">最近反馈：{g.feedback[0].comment || g.feedback[0].source}</div>}
            </div>
          );
        })}
      </div>
    </>
  );
}

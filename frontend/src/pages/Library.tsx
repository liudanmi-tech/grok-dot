import { useState } from "react";
import { Clock, MessageCircle, Shield, UserPlus, X } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";
import { Avatar } from "../ui";

const REGIONS = ["全国", "Lagos", "North", "South-West", "South-East", "FCT"];

export default function Library({ go, onHired }: { go: (p: string) => void; onHired: () => void }) {
  const { data, reload } = usePoll<any[]>("/bot-templates", 0);
  const [pick, setPick] = useState<any>(null);
  const [name, setName] = useState("");
  const [region, setRegion] = useState("全国");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const open = (t: any) => { setPick(t); setName(""); setRegion("全国"); setErr(null); };
  const hire = async () => {
    setErr(null); setBusy(true);
    try {
      const r = await post("/bots", { template: pick.id, display_name: name.trim() || undefined, region: region === "全国" ? undefined : region });
      onHired(); reload(); setPick(null); go(`bot/${r.id}`);
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>智能体库</h1>
          <p>每个岗位都带着固定的技能、工具白名单和审批规则。招募后即在岗，权限按区域收窄，所有动作进审计。</p>
        </div>
      </div>

      <div className="grid g3">
        {data?.map((t) => {
          const full = !t.multi_instance && t.hired > 0;
          return (
            <div key={t.id} className="card agent" style={{ cursor: "default" }}>
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <Avatar bot={{ color: t.color, display_name: t.name }} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600 }}>{t.name}</div>
                  <div className="muted small">{t.title}</div>
                </div>
              </div>
              <div className="small">{t.summary}</div>
              <div className="row">{t.capabilities.map((c: string) => <span key={c} className="tag">{c}</span>)}</div>
              <div className="row muted small">
                <span className="pill accent"><Shield size={11} />{t.autonomy}</span>
                {t.schedule && <span><Clock size={11} /> {t.schedule}</span>}
                {t.channel !== "内部" && <span><MessageCircle size={11} /> {t.channel}</span>}
                {t.phase && <span className="tag">{t.phase}</span>}
              </div>
              <div className="row between" style={{ marginTop: "auto" }}>
                <span className="muted small">{t.hired ? `团队中已有 ${t.hired} 名` : "尚未招募"} · {t.tools.length} 个工具</span>
                <button className="btn primary sm" disabled={full} onClick={() => open(t)}>
                  <UserPlus size={13} />{full ? "仅限一名" : "招募"}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {pick && (
        <div className="modal-bg" onClick={() => setPick(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="row between">
              <div className="row"><Avatar bot={{ color: pick.color, display_name: pick.name }} /><div><b>招募 {pick.name}</b><div className="muted small">{pick.title}</div></div></div>
              <button className="btn ghost sm" onClick={() => setPick(null)}><X size={14} /></button>
            </div>
            <div className="field">
              <label>名字</label>
              <input value={name} placeholder={region === "全国" ? pick.name : `${pick.name} · ${region}`} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field">
              <label>负责区域（数据权限收窄到该区域）</label>
              <div className="row">{REGIONS.map((r) => (
                <span key={r} className={`mention ${region === r ? "on" : ""}`} onClick={() => setRegion(r)}>{r}</span>
              ))}</div>
            </div>
            <div className="muted small">工具白名单：{pick.tools.join("、")}</div>
            {err && <div className="err">{err}</div>}
            <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
              <button className="btn" onClick={() => setPick(null)}>取消</button>
              <button className="btn primary" disabled={busy} onClick={hire}><UserPlus size={14} />确认招募</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

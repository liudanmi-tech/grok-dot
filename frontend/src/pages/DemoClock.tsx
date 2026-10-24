import { FastForward, Moon, RotateCcw, Sun } from "lucide-react";
import { post } from "../api";
import { usePoll } from "../hooks";

// 演示用：时间快进（晨会 07:30 / 晚会 18:00 / 下一天）
export default function DemoClock({ onChange }: { onChange?: () => void }) {
  const { data, reload } = usePoll<any>("/demo/clock", 5000);
  if (!data) return null;
  const go = async (to: string) => { try { await post("/demo/clock", { to }); } finally { reload(); onChange?.(); } };
  const t = new Date(data.now);
  const label = data.now.slice(5, 10) + " " + data.now.slice(11, 16);
  return (
    <div className="democlock">
      <span className={`phase ${data.phase}`}>{data.phase === "standup" ? <Sun size={13} /> : data.phase === "review" ? <Moon size={13} /> : <FastForward size={13} />}</span>
      <span>拉各斯 <b>{label}</b>{data.offset_days > 0 && <span className="muted"> · 已快进 {data.offset_days} 天</span>}</span>
      <span className="sep" />
      <button className="btn sm ghost" onClick={() => go("review")} title="快进到今天 18:00 晚会">快进到晚会</button>
      <button className="btn sm ghost" onClick={() => go("next_day")} title="快进到明天 07:30 晨会">下一天晨会</button>
      {data.offset_days > 0 && <button className="btn sm ghost" onClick={() => go("reset")} title="回到真实时间"><RotateCcw size={12} /></button>}
      <span hidden>{t.getTime()}</span>
    </div>
  );
}

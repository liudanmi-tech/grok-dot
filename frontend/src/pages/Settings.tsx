import { useEffect, useMemo, useState } from "react";
import { api, fmtTime, post } from "../api";
import { usePoll } from "../hooks";

type Slot = "standard" | "economy" | "premium";
const SLOTS: Slot[] = ["standard", "economy", "premium"];
const SLOT_NAME: Record<Slot, string> = { standard: "标准档", economy: "经济档", premium: "高级档" };
const PRESETS: Record<Slot, string[]> = {
  standard: ["qwen/qwen3.8-flash", "qwen/qwen-plus", "openai/gpt-4o-mini", "google/gemini-2.5-flash"],
  economy: ["qwen/qwen3.7-flash", "google/gemini-2.5-flash-lite", "deepseek/deepseek-chat"],
  premium: ["qwen/qwen3.8-max-0902", "anthropic/claude-sonnet-4.5", "google/gemini-2.5-pro"],
};

export default function Settings() {
  const s = usePoll<any>("/settings/models", 0);
  const [key, setKey] = useState("");
  const [models, setModels] = useState<Record<Slot, string> | null>(null);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tests, setTests] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (s.data && !models) setModels(s.data.models); }, [s.data, models]);
  useEffect(() => { api<any[]>("/settings/openrouter/models").then(setCatalog).catch(() => setCatalog([])); }, []);
  const price = useMemo(() => Object.fromEntries(catalog.map((m) => [m.id, m])), [catalog]);

  const save = async (body: any, done: string) => {
    setErr(null); setMsg(null); setBusy(true);
    try { await api("/settings/models", { method: "PUT", body: JSON.stringify(body) }); setKey(""); setMsg(done); s.reload(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };
  const test = async (slot: Slot) => {
    setTests((t) => ({ ...t, [slot]: { running: true } }));
    try { const r = await post("/settings/models/test", { slot }); setTests((t) => ({ ...t, [slot]: r })); }
    catch (e: any) { setTests((t) => ({ ...t, [slot]: { ok: false, error: e.message } })); }
  };

  if (!s.data || !models) return <div className="card muted">加载中…</div>;
  const d = s.data;
  const ro = !d.can_edit;

  return (
    <>
      <header className="top"><h2>模型设置</h2>
        <span className={`pill ${d.configured ? "good" : "warn"}`}>{d.configured ? `OpenRouter 已配置 · ${d.key_hint}` : "OpenRouter 未配置：当前回退到通义千问或模拟模型"}</span>
      </header>
      {ro && <div className="card muted">只有智能体运营或国家经理可以修改模型设置（左下角切换用户）。</div>}

      <div className="card">
        <h3 style={{ marginTop: 0 }}>OpenRouter API Key</h3>
        <div className="muted" style={{ marginBottom: 8 }}>
          在 openrouter.ai → Keys 创建。Key 保存后只显示前后几位，不会再完整返回给浏览器；修改记入审计（不含 Key）。
          {d.updated_by && <> 最近修改：{d.updated_by} · {fmtTime(d.updated_at)}</>}
        </div>
        <div className="row">
          <input type="password" autoComplete="off" style={{ flex: 1, minWidth: 280 }} disabled={ro}
            placeholder={d.configured ? `已配置 ${d.key_hint}，输入新 Key 可替换` : "sk-or-v1-..."} value={key} onChange={(e) => setKey(e.target.value)} />
          <button className="btn primary" disabled={ro || busy || !key} onClick={() => save({ api_key: key }, "Key 已保存")}>保存 Key</button>
          {d.source === "console" && <button className="btn danger" disabled={ro || busy} onClick={() => save({ clear_key: true }, "Key 已清除")}>清除</button>}
        </div>
      </div>

      <div className="card">
        <h3 style={{ marginTop: 0 }}>各档位使用的模型</h3>
        <div className="muted" style={{ marginBottom: 8 }}>填写 OpenRouter 的模型 ID（厂商/模型）。价格为 OpenRouter 公布的每百万 token 美元价格；实际费用以 OpenRouter 计费为准，记入“总览”的本月费用。</div>
        <datalist id="or-models">{catalog.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</datalist>
        <table>
          <thead><tr><th>档位</th><th>用途</th><th>模型 ID</th><th>价格（输入 / 输出，$/百万 token）</th><th>测试</th></tr></thead>
          <tbody>{SLOTS.map((slot) => {
            const p = price[models[slot]];
            const t = tests[slot];
            return (
              <tr key={slot}>
                <td><b>{SLOT_NAME[slot]}</b></td>
                <td className="muted">{d.slots[slot]}</td>
                <td>
                  <input list="or-models" style={{ width: 260 }} disabled={ro} value={models[slot]}
                    onChange={(e) => setModels({ ...models, [slot]: e.target.value })} />
                  <div className="row" style={{ marginTop: 4 }}>{PRESETS[slot].map((m) => (
                    <button key={m} className="btn" style={{ fontSize: 11, padding: "2px 6px" }} disabled={ro} onClick={() => setModels({ ...models, [slot]: m })}>{m}</button>
                  ))}</div>
                </td>
                <td className="muted">{p ? `$${p.input_per_m} / $${p.output_per_m}` : catalog.length ? <span className="err">列表中没有这个 ID</span> : "—"}</td>
                <td style={{ minWidth: 160 }}>
                  <button className="btn" disabled={ro || !d.configured || t?.running} onClick={() => test(slot)}>测试</button>
                  {t && !t.running && (t.ok
                    ? <div style={{ color: "var(--good)", fontSize: 12 }}>✓ {t.model} · {t.latency_ms}ms{t.cost_usd != null ? ` · $${t.cost_usd}` : ""}</div>
                    : <div className="err" style={{ fontSize: 12 }}>{t.error}</div>)}
                </td>
              </tr>
            );
          })}</tbody>
        </table>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={ro || busy} onClick={() => save({ models }, "模型设置已保存")}>保存模型设置</button>
          <span className="muted">保存后立即生效，无需重启。测试只验证所选档位能否调通，不走降级链。</span>
        </div>
      </div>
      {msg && <div className="card" style={{ color: "var(--good)" }}>{msg}</div>}
      {err && <div className="card err">{err}</div>}
      <div className="card muted">
        降级顺序：OpenRouter 所选模型 → 通义千问（如配置） → 模拟模型。任一步失败自动切换并写入审计（model.fallback）。
        数据合规提示：OpenRouter 会把请求转发给对应模型厂商，正式处理经销商个人数据前请法务确认。
      </div>
    </>
  );
}

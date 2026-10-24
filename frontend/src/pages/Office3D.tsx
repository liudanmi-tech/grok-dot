// 3D 虚拟办公室：Kenney CC0 低多边形家具 + 人物（public/office）。数据与 2D 看板同源（/kpis/:id）。
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Html, OrbitControls, useAnimations, useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { clone as cloneSkinned } from "three/examples/jsm/utils/SkeletonUtils.js";
import { usePoll } from "../hooks";
import { initials } from "../ui";

const F = (n: string) => `/office/furniture/${n}.glb`;
const CHARS = ["male-a", "female-a", "male-b", "female-b", "male-c", "female-c", "male-d", "female-d", "male-e", "female-e", "male-f", "female-f"]
  .map((c) => `/office/characters/character-${c}.glb`);

type V3 = [number, number, number];
// 所有坐标用模型原始单位（Kenney 家具与人物同比例），整个场景统一放大 SCALE 倍
const SCALE = 1.6;
const SPEED = 0.75;         // 走路速度（场景单位/秒）
const DESK_COLS = 4;
const deskPos = (i: number): V3 => [-1.55 + (i % DESK_COLS) * 1.05, 0, 0.35 + Math.floor(i / DESK_COLS) * 1.3];
const LEAD_DESK: V3 = [0.05, 0, -1.25];
const MEETING: V3 = [-2.45, 0, -1.85];
const meetSeat = (i: number, n: number): V3 => {
  const a = (i / Math.max(n, 1)) * Math.PI * 2 + 0.3;
  return [MEETING[0] + Math.cos(a) * 1.05, 0, MEETING[2] + Math.sin(a) * 0.78];
};
const LOUNGE: V3[] = [[2.15, 0, -1.25], [2.85, 0, -1.3], [2.4, 0, -0.75], [3.3, 0, -1.75], [2.9, 0, -0.45], [3.45, 0, -1.0]];
const EXIT: V3 = [3.9, 0, 3.0];

// ---------- 寻路：家具当障碍，A* 走网格，再拉直路径（不再穿桌子） ----------
const ROOM = { x0: -4.05, x1: 4.05, z0: -3.15, z1: 3.15 };
const CELL = 0.1, PAD = 0.14;
type Rect = [number, number, number, number]; // x0, x1, z0, z1
function obstacles(nDesks: number): Rect[] {
  const r: Rect[] = [];
  for (let i = 0; i < nDesks; i++) { const [x, , z] = deskPos(i); r.push([x - 0.37, x + 0.37, z - 0.28, z + 0.28]); }
  r.push([LEAD_DESK[0] - 0.37, LEAD_DESK[0] + 0.37, LEAD_DESK[2] - 0.28, LEAD_DESK[2] + 0.28]);
  r.push([MEETING[0] - 0.62, MEETING[0] + 0.62, MEETING[2] - 0.33, MEETING[2] + 0.33]);   // 会议桌
  r.push([1.85, 2.83, -2.78, -2.33]);   // 沙发
  r.push([2.0, 2.62, -2.17, -1.73]);    // 茶几
  r.push([3.0, 3.88, -3.2, -2.93]);     // 吧台
  r.push([0.9, 1.32, -3.5, -3.18]);     // 书架
  return r;
}
function makeGrid(rects: Rect[]) {
  const W = Math.round((ROOM.x1 - ROOM.x0) / CELL), H = Math.round((ROOM.z1 - ROOM.z0) / CELL);
  const g = new Uint8Array(W * H);
  for (const [x0, x1, z0, z1] of rects)
    for (let i = Math.max(0, Math.floor((x0 - PAD - ROOM.x0) / CELL)); i <= Math.min(W - 1, Math.floor((x1 + PAD - ROOM.x0) / CELL)); i++)
      for (let j = Math.max(0, Math.floor((z0 - PAD - ROOM.z0) / CELL)); j <= Math.min(H - 1, Math.floor((z1 + PAD - ROOM.z0) / CELL)); j++) g[j * W + i] = 1;
  return { W, H, g };
}
const cellOf = (G: any, x: number, z: number) => [Math.min(G.W - 1, Math.max(0, Math.floor((x - ROOM.x0) / CELL))), Math.min(G.H - 1, Math.max(0, Math.floor((z - ROOM.z0) / CELL)))];
const centerOf = (i: number, j: number): V3 => [ROOM.x0 + (i + 0.5) * CELL, 0, ROOM.z0 + (j + 0.5) * CELL];
function free(G: any, i: number, j: number, s: number, t: number) {
  // 起点 / 终点附近（工位座椅就在桌边）允许通行
  if (i < 0 || j < 0 || i >= G.W || j >= G.H) return false;
  const k = j * G.W + i;
  return !G.g[k] || k === s || k === t || Math.abs(k - s) <= 2 || Math.abs(k - t) <= 2;
}
function lineFree(G: any, a: V3, b: V3, s: number, t: number) {
  const n = Math.ceil(Math.hypot(b[0] - a[0], b[2] - a[2]) / (CELL / 2));
  for (let k = 1; k < n; k++) {
    const [i, j] = cellOf(G, a[0] + ((b[0] - a[0]) * k) / n, a[2] + ((b[2] - a[2]) * k) / n);
    if (!free(G, i, j, s, t)) return false;
  }
  return true;
}
function findPath(G: any, from: V3, to: V3): V3[] {
  const [si, sj] = cellOf(G, from[0], from[2]), [ti, tj] = cellOf(G, to[0], to[2]);
  const s = sj * G.W + si, t = tj * G.W + ti;
  if (lineFree(G, from, to, s, t)) return [to];
  const N = G.W * G.H, gScore = new Float32Array(N).fill(Infinity), came = new Int32Array(N).fill(-1), closed = new Uint8Array(N);
  const open: number[] = [s]; gScore[s] = 0;
  const h = (k: number) => Math.hypot((k % G.W) - ti, Math.floor(k / G.W) - tj);
  const DIRS = [[1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, 1.414], [1, -1, 1.414], [-1, 1, 1.414], [-1, -1, 1.414]];
  let guard = 0;
  while (open.length && guard++ < 20000) {
    let bi = 0;
    for (let q = 1; q < open.length; q++) if (gScore[open[q]] + h(open[q]) < gScore[open[bi]] + h(open[bi])) bi = q;
    const cur = open.splice(bi, 1)[0];
    if (cur === t) break;
    closed[cur] = 1;
    const ci = cur % G.W, cj = Math.floor(cur / G.W);
    for (const [di, dj, c] of DIRS) {
      const ni = ci + di, nj = cj + dj;
      if (!free(G, ni, nj, s, t)) continue;
      const nk = nj * G.W + ni;
      if (closed[nk]) continue;
      const ng = gScore[cur] + c;
      if (ng < gScore[nk]) { gScore[nk] = ng; came[nk] = cur; if (!open.includes(nk)) open.push(nk); }
    }
  }
  if (came[t] === -1) return [to];
  const cells: V3[] = [];
  for (let k = t; k !== s && k !== -1; k = came[k]) cells.push(centerOf(k % G.W, Math.floor(k / G.W)));
  cells.reverse(); cells[cells.length - 1] = to;
  // 拉直：能直线走到的点就跳过中间点
  const out: V3[] = [];
  let a = from;
  for (let k = 0; k < cells.length; k++) {
    let far = k;
    while (far + 1 < cells.length && lineFree(G, a, cells[far + 1], s, t)) far++;
    out.push(cells[far]); a = cells[far]; k = far;
  }
  return out;
}

function hashIdx(s: string, n: number) {
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return h % n;
}

// ---------- 家具 ----------
function Model({ name, position, rotation = 0, scale = 1 }: { name: string; position: V3; rotation?: number; scale?: number }) {
  const { scene } = useGLTF(F(name));
  const obj = useMemo(() => {
    const o = scene.clone(true);
    o.traverse((m: any) => { if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; } });
    return o;
  }, [scene]);
  return <primitive object={obj} position={position} rotation={[0, rotation, 0]} scale={scale} />;
}

function Workstation({ at, glow, label }: { at: V3; glow?: string | null; label?: React.ReactNode }) {
  const [x, , z] = at;
  return (
    <group>
      <Model name="desk" position={[x - 0.36, 0, z + 0.1]} />
      <Model name="computerScreen" position={[x - 0.2, 0.38, z - 0.12]} />
      <Model name="computerKeyboard" position={[x - 0.14, 0.38, z + 0.05]} />
      <Model name="chairDesk" position={[x - 0.07, 0, z + 0.48]} rotation={Math.PI} />
      {glow && (
        <mesh position={[x - 0.005, 0.53, z - 0.113]}>
          <planeGeometry args={[0.34, 0.22]} />
          <meshBasicMaterial color={glow} toneMapped={false} />
        </mesh>
      )}
      {label}
    </group>
  );
}

function Room({ market }: { market: string }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow position={[0, -0.001, 0]}>
        <planeGeometry args={[8.4, 6.6]} />
        <meshStandardMaterial color="#ece6dd" />
      </mesh>
      {/* 工位区地毯、会议区地毯、休息区地毯 */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0.03, 0.002, 0.75]} receiveShadow>
        <planeGeometry args={[4.6, 2.9]} />
        <meshStandardMaterial color="#d8dee9" />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[MEETING[0], 0.002, MEETING[2]]} receiveShadow>
        <circleGeometry args={[1.35, 40]} />
        <meshStandardMaterial color="#e8dac6" />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[2.55, 0.002, -1.6]} receiveShadow>
        <planeGeometry args={[2.0, 1.6]} />
        <meshStandardMaterial color="#dfe9df" />
      </mesh>
      {/* 墙 */}
      <mesh position={[0, 0.8, -3.3]} receiveShadow><boxGeometry args={[8.4, 1.6, 0.08]} /><meshStandardMaterial color="#f7f4ef" /></mesh>
      <mesh position={[-4.2, 0.8, 0]} receiveShadow><boxGeometry args={[0.08, 1.6, 6.6]} /><meshStandardMaterial color="#f2eee7" /></mesh>
      {[-0.2, 1.6, 3.2].map((x) => (
        <mesh key={x} position={[x, 0.95, -3.25]}><planeGeometry args={[1.0, 0.55]} /><meshStandardMaterial color="#bfdbfe" emissive="#93c5fd" emissiveIntensity={0.25} /></mesh>
      ))}
      {/* 会议区 */}
      <Model name="tableCross" position={[MEETING[0] - 0.61, 0, MEETING[2] + 0.32]} scale={0.6} />
      {/* 茶水与休息区 */}
      <Model name="kitchenBar" position={[3.0, 0, -2.95]} />
      <Model name="kitchenBar" position={[3.43, 0, -2.95]} />
      <Model name="kitchenCoffeeMachine" position={[3.1, 0.42, -3.05]} />
      <Model name="loungeSofa" position={[1.85, 0, -2.35]} />
      <Model name="tableCoffee" position={[2.0, 0, -1.75]} />
      <Model name="pottedPlant" position={[-3.95, 0, -2.95]} />
      <Model name="pottedPlant" position={[3.85, 0, 2.9]} />
      <Model name="pottedPlant" position={[-3.95, 0, 2.9]} />
      <Model name="plantSmall1" position={[0.9, 0.38, -1.2]} />
      <Model name="bookcaseOpen" position={[0.9, 0, -3.22]} />
      <Model name="lampSquareFloor" position={[1.45, 0, -3.1]} />
      <Html zIndexRange={[10, 0]} position={[3.4, 1.45, -3.24]} transform distanceFactor={3.2} occlude={false}>
        <div className="o3-sign">{market} 销售中心</div>
      </Html>
    </group>
  );
}

// ---------- 墙上看板 ----------
function KpiBoard({ k }: { k: any }) {
  const p = k.progress;
  const pct = Math.min(100, (100 * p.actual) / Math.max(1, p.target_total));
  const exp = Math.min(100, (100 * p.expected) / Math.max(1, p.target_total));
  return (
    <Html zIndexRange={[10, 0]} position={[-1.2, 0.98, -3.24]} transform distanceFactor={2.6} occlude={false}>
      <div className="o3-board">
        <div className="o3-board-title">{k.title}</div>
        <div className="o3-big">{p.actual.toLocaleString()}<span> / {p.target_total.toLocaleString()} 台</span></div>
        <div className="o3-bar"><i style={{ width: `${pct}%` }} className={p.rag || ""} /><b style={{ left: `${exp}%` }} /></div>
        <div className="o3-row"><span>节奏 {p.pace == null ? "—" : Math.round(p.pace * 100) + "%"}</span><span>较基线 {p.lift_pct == null ? "—" : p.lift_pct + "%"}</span><span>第 {p.days_elapsed}/{p.days_total} 天</span></div>
      </div>
    </Html>
  );
}

function ChannelScreen({ channel }: { channel: any[] }) {
  const last = channel.slice(-6);
  return (
    <Html zIndexRange={[10, 0]} position={[-4.14, 0.92, 0.55]} rotation={[0, Math.PI / 2, 0]} transform distanceFactor={2.6} occlude={false}>
      <div className="o3-screen">
        <div className="o3-screen-title">团队群 · 实时</div>
        {last.map((c) => <div key={c.id} className="o3-msg"><b>{c.author_name}</b>{c.text.replace(/\n/g, " ").slice(0, 70)}</div>)}
        {!last.length && <div className="o3-msg">暂无消息</div>}
      </div>
    </Html>
  );
}

// ---------- 人物 ----------
type Role = { id: string; name: string; color: string; activity: string; current: any; attention: number; approvals: number; done_today: number };

function Person({ bot, url, home, target, pose, faceTo, bubble, onClick, grid, card }: {
  bot: Role; url: string; home: V3; target: V3; pose: "sit" | "idle" | "type"; faceTo: number; bubble: string | null; onClick: () => void; grid: any;
  card?: React.ReactNode;
}) {
  const { scene, animations } = useGLTF(url);
  const model = useMemo(() => {
    const m = cloneSkinned(scene) as THREE.Object3D;
    m.traverse((o: any) => { if (o.isMesh) { o.castShadow = true; } });
    return m;
  }, [scene]);
  const group = useRef<THREE.Group>(null!);
  const { actions } = useAnimations(animations, group);
  const [moving, setMoving] = useState(false);
  const cur = useRef<string | null>(null);
  const celebrate = useRef(0);
  const lastDone = useRef(bot.done_today);
  useEffect(() => {
    if (bot.done_today > lastDone.current) celebrate.current = 2.2;  // 刚交付：庆祝一下
    lastDone.current = bot.done_today;
  }, [bot.done_today]);
  useEffect(() => { if (group.current) group.current.position.set(...home); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // 目标一变就重新寻路
  const path = useRef<V3[]>([]);
  const tkey = target.join(",");
  useEffect(() => {
    const g = group.current;
    if (!g) return;
    path.current = findPath(grid, [g.position.x, 0, g.position.z], target);
  }, [tkey, grid]); // eslint-disable-line react-hooks/exhaustive-deps

  const play = (name: string) => {
    if (cur.current === name) return;
    const next = actions[name];
    if (!next) return;
    next.reset().fadeIn(0.25).play();
    if (cur.current && actions[cur.current]) actions[cur.current]!.fadeOut(0.25);
    cur.current = name;
  };

  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    while (path.current.length > 1 && Math.hypot(path.current[0][0] - g.position.x, path.current[0][2] - g.position.z) < 0.04) path.current.shift();
    const wp = path.current[0] || target;
    const to = new THREE.Vector3(...wp);
    const d = to.clone().sub(g.position);
    d.y = 0;
    const dist = d.length();
    if (dist > 0.03) {
      const step = Math.min(dist, SPEED * dt);
      g.position.add(d.normalize().multiplyScalar(step));
      g.rotation.y = THREE.MathUtils.lerp(g.rotation.y, Math.atan2(d.x, d.z), 0.2);
      if (!moving) setMoving(true);
      play("walk");
      return;
    }
    if (moving) setMoving(false);
    g.rotation.y = THREE.MathUtils.lerp(g.rotation.y, faceTo, 0.15);
    if (celebrate.current > 0) { celebrate.current -= dt; play("emote-yes"); return; }
    play(pose === "sit" || pose === "type" ? "sit" : bot.activity === "needs_you" ? "emote-no" : "idle");
    // 打字：坐姿下双手轻微起伏
    if (pose === "type") {
      const t = performance.now() / 1000;
      g.traverse((o: any) => {
        if (o.name === "arm-left") o.rotation.x = -1.1 + Math.sin(t * 14) * 0.08;
        if (o.name === "arm-right") o.rotation.x = -1.1 + Math.sin(t * 14 + 1.4) * 0.08;
      });
    }
  });

  const pct = bot.current?.progress_total ? (100 * bot.current.progress_done) / bot.current.progress_total : 0;
  return (
    <group ref={group} onClick={(e) => { e.stopPropagation(); onClick(); }}
      onPointerOver={() => { document.body.style.cursor = "pointer"; }} onPointerOut={() => { document.body.style.cursor = ""; }}>
      <primitive object={model} scale={0.85} />
      {card && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.01, 0]}>
          <ringGeometry args={[0.28, 0.34, 40]} />
          <meshBasicMaterial color={bot.color} transparent opacity={0.85} />
        </mesh>
      )}
      {card}
      <Html position={[0, 0.95, 0]} center distanceFactor={7} occlude={false} zIndexRange={[20, 0]}>
        <div className={`o3-tag ${bot.activity}`} onClick={onClick}>
          {bubble && <div className="o3-bubble">{bubble}</div>}
          <div className="o3-name"><span className="o3-av" style={{ background: bot.color }}>{initials(bot.name)}</span>{bot.name.split(" · ")[0]}</div>
          {bot.current && <div className="o3-task">{bot.current.goal.slice(0, 26)}…</div>}
          {bot.current && <div className="o3-mini"><i style={{ width: `${Math.max(6, pct)}%` }} /></div>}
        </div>
      </Html>
    </group>
  );
}

// ---------- 人物卡片：点人物弹出（KPI 举措、现在 / 近期在做什么、等级评价、跳转对话） ----------
const RATING: Record<string, string> = { good: "超出预期", ok: "符合预期", behind: "落后" };
function PersonCard({ botId, k, go, onClose }: { botId: string; k: any; go: (p: string) => void; onClose: () => void }) {
  const { data: b } = usePoll<any>(`/bots/${botId}`, 4000);
  const week = k.weeks.find((w: any) => w.week_no === k.week_no) || k.weeks[k.weeks.length - 1];
  const item = week?.items?.find((i: any) => i.bot_id === botId);
  const lead = b?.template === "chief-of-staff";
  const tasks = b?.tasks || [];
  const open = tasks.find((t: any) => !["delivered", "accepted", "failed", "cancelled"].includes(t.status));
  const chatOf = (t: any) => t.parent?.task_id || t.task_id;
  const target = open || tasks[0];
  const g = b?.growth;
  const pct = (t: any) => (t?.progress_total ? Math.round((100 * t.progress_done) / t.progress_total) : 0);
  const stop = (e: any) => e.stopPropagation();
  return (
    <Html position={[0, 1.15, 0]} center distanceFactor={5.5} zIndexRange={[60, 40]} occlude={false}>
      <div className="o3-card" onPointerDown={stop} onClick={stop} onWheel={stop}>
        {!b ? <div className="o3-card-body">加载中…</div> : (
          <>
            <div className="o3-card-head" style={{ background: `linear-gradient(135deg, ${b.color}, ${b.color}cc)` }}>
              <span className="o3-av lg">{initials(b.display_name)}</span>
              <div style={{ minWidth: 0 }}>
                <div className="o3-card-name">{b.display_name}</div>
                <div className="o3-card-sub">{b.title}</div>
              </div>
              {g && <span className="o3-level">{g.level}</span>}
              <button className="o3-x" onClick={onClose}>×</button>
            </div>
            <div className="o3-card-body">
              <div className="o3-sec">
                <div className="o3-label">{lead ? "KPI 目标" : `本周 KPI 举措 · 第 ${week?.week_no || "-"} 周`}</div>
                {lead ? <div>{k.title} · 已完成 {k.progress.actual.toLocaleString()} / {k.progress.target_total.toLocaleString()} 台</div>
                  : item ? <><div className="o3-clamp">{item.objective}</div><div className="o3-muted o3-clamp">衡量：{item.measure}</div></> : <div className="o3-muted">不在本 KPI 小队的周计划里</div>}
              </div>
              <div className="o3-sec">
                <div className="o3-label">现在</div>
                {open ? <>
                  <div className="o3-clip">{open.goal}</div>
                  <div className="o3-pbar"><i style={{ width: `${Math.max(5, pct(open))}%` }} /></div>
                  <div className="o3-muted">{open.status_label} · {open.progress_done}/{open.progress_total} 步</div>
                </> : <div className="o3-muted">{b.activity === "off_duty" ? "已下班" : "手上没有进行中的任务"}</div>}
              </div>
              <div className="o3-sec">
                <div className="o3-label">近期</div>
                {tasks.filter((t: any) => t !== open).slice(0, 3).map((t: any) => (
                  <div key={t.task_id} className="o3-row2" onClick={() => go(`chat/${chatOf(t)}`)}>
                    <span className={`o3-dot ${t.status}`} /><span className="o3-clip">{t.goal}</span><span className="o3-muted">{t.status_label}</span>
                  </div>
                ))}
                {tasks.length <= (open ? 1 : 0) && <div className="o3-muted">暂无</div>}
              </div>
              {g && (
                <div className="o3-sec">
                  <div className="o3-label">整体评价</div>
                  <div className="o3-stats">
                    <span><b>{g.delivered}</b>交付</span><span><b>{g.accepted}</b>验收</span>
                    <span><b>{g.ratings.good}</b>超出预期</span><span><b>{g.ratings.ok}</b>符合</span><span><b>{g.ratings.behind}</b>落后</span>
                  </div>
                  <div className="o3-pbar xp"><i style={{ width: `${g.next_xp ? Math.min(100, (100 * g.xp) / g.next_xp) : 100}%` }} /></div>
                  <div className="o3-muted">{g.xp}{g.next_xp ? ` / ${g.next_xp}` : ""} XP{g.feedback[0] ? ` · 最近：${RATING[g.feedback[0].score] || ""}${g.feedback[0].comment ? "，" + g.feedback[0].comment.slice(0, 40) : ""}` : ""}</div>
                </div>
              )}
            </div>
            <div className="o3-card-foot">
              <button className="o3-btn primary" disabled={!target} onClick={() => target && go(`chat/${chatOf(target)}`)}>进入对话 →</button>
              <button className="o3-btn" onClick={() => go(`bot/${botId}`)}>工位详情</button>
            </div>
          </>
        )}
      </div>
    </Html>
  );
}

// ---------- 主场景 ----------
export default function Office3D({ kpiId, go }: { kpiId: string; go: (p: string) => void }) {
  const { data: k } = usePoll<any>(`/kpis/${kpiId}`, 2500);
  const bots = usePoll<any[]>("/bots", 3000);
  const nDesks = k?.squad_cards?.length || 0;
  const grid = useMemo(() => makeGrid(obstacles(nDesks)), [nDesks]);
  const [sel, setSel] = useState<string | null>(null);
  const clock = usePoll<any>(`/demo/clock?market=${k?.market?.code || "NG"}`, 5000);
  if (!k) return <div className="card muted">加载 3D 办公室…</div>;

  const squad: Role[] = k.squad_cards.map((b: any) => ({ id: b.id, name: b.display_name, color: b.color, activity: b.activity, current: b.current,
    attention: b.attention, approvals: b.approvals, done_today: b.done_today }));
  const now = clock.data?.now ? new Date(clock.data.now).getTime() : Date.now();
  const today = clock.data?.now?.slice(0, 10);  // KPI 所在市场的当地日期
  const standup = k.meetings.find((m: any) => m.kind === "standup" && m.day === today);
  const review = k.meetings.find((m: any) => m.kind === "review" && m.day === today);
  // 会议进行中：晨会开完 25 分钟内大家围坐；晚会材料备好、主管还没提交之前也在会议桌
  const since = standup ? now - new Date(standup.created_at).getTime() : -1;
  const inStandup = since >= 0 && since < 25 * 60 * 1000;
  const inReview = !!review && review.status === "draft";
  const meeting = inReview ? "晚会复盘中" : inStandup ? "晨会进行中" : null;

  const people = squad.map((b, i) => {
    const desk = deskPos(i);
    const seat: V3 = [desk[0], 0, desk[2] + 0.42];
    if (meeting) {
      const s = meetSeat(i, squad.length + 1);
      return { b, home: seat, target: s, pose: "idle" as const, face: Math.atan2(MEETING[0] - s[0], MEETING[2] - s[2]), desk };
    }
    if (b.activity === "working" || b.activity === "paused")
      return { b, home: seat, target: seat, pose: (b.activity === "working" ? "type" : "sit") as "type" | "sit", face: Math.PI, desk };
    if (b.activity === "needs_you") return { b, home: seat, target: [desk[0] + 0.42, 0, desk[2] + 0.45] as V3, pose: "idle" as const, face: 0, desk };
    if (b.activity === "off_duty") return { b, home: seat, target: EXIT, pose: "idle" as const, face: 0, desk, gone: true };
    const l = LOUNGE[i % LOUNGE.length];
    return { b, home: seat, target: l, pose: "idle" as const, face: hashIdx(b.id, 6), desk };
  });

  // 总调度：主位上的负责人。干活时坐在主位打字，空闲时站在主位旁，开会坐会议桌上首，下班离开
  const chiefCard = (bots.data || []).find((b: any) => b.template === "chief-of-staff" && b.state !== "retired");
  const chief: Role | null = chiefCard ? { id: chiefCard.id, name: chiefCard.display_name, color: chiefCard.color, activity: chiefCard.activity,
    current: chiefCard.current, attention: chiefCard.attention, approvals: chiefCard.approvals, done_today: chiefCard.done_today } : null;
  const leadSeat: V3 = [LEAD_DESK[0], 0, LEAD_DESK[2] + 0.42];
  const chiefPlan = !chief ? null : meeting
    ? { target: meetSeat(squad.length, squad.length + 1), pose: "idle" as const, face: Math.atan2(MEETING[0] - meetSeat(squad.length, squad.length + 1)[0], MEETING[2] - meetSeat(squad.length, squad.length + 1)[2]) }
    : chief.activity === "working" ? { target: leadSeat, pose: "type" as const, face: Math.PI }
    : chief.activity === "off_duty" ? null
    : { target: [LEAD_DESK[0] + 0.5, 0, LEAD_DESK[2] + 0.45] as V3, pose: "idle" as const, face: 0.4 };

  const counts = squad.reduce((a: any, b) => ({ ...a, [b.activity]: (a[b.activity] || 0) + 1 }), {});
  const bubbleOf = (b: Role) => b.activity === "needs_you" ? `❗ ${b.attention + b.approvals} 件事等你` : null;

  return (
    <div className="o3-wrap">
      <div className="o3-hud">
        <b>{k.market?.city || ""} {clock.data?.now?.slice(11, 16)}</b>
        {meeting ? <span className="pill info">{meeting}</span> : <span className="pill">{clock.data?.phase === "review" ? "下班时间" : "办公时间"}</span>}
        <span className="muted small">干活中 {counts.working || 0} · 待命 {counts.idle || 0} · 等你 {counts.needs_you || 0} · 下班 {counts.off_duty || 0}</span>
        <span className="muted small" style={{ marginLeft: "auto" }}>拖动旋转 · 滚轮缩放 · 点人物看卡片</span>
      </div>
      <Canvas shadows camera={{ position: [8.5, 9.5, 11], fov: 30 }} dpr={[1, 2]} onPointerMissed={(e: any) => { if (!(e.target as HTMLElement)?.closest?.(".o3-tag, .o3-card")) setSel(null); }}>
        <color attach="background" args={["#eef2f7"]} />
        <hemisphereLight args={["#ffffff", "#c9c2b6", 0.9]} />
        <directionalLight position={[6, 10, 4]} intensity={1.4} castShadow shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-9} shadow-camera-right={9} shadow-camera-top={9} shadow-camera-bottom={-9} />
        <Suspense fallback={<Html zIndexRange={[10, 0]} center><div className="muted">加载模型…</div></Html>}>
          <group scale={SCALE}>
          <Room market={k.market?.name || ""} />
          <KpiBoard k={k} />
          <ChannelScreen channel={k.channel} />
          {/* 总调度的主位 */}
          <Workstation at={LEAD_DESK} glow="#a5b4fc" label={
            chief?.activity === "off_duty" && !meeting
              ? <Html zIndexRange={[10, 0]} position={[LEAD_DESK[0], 0.7, LEAD_DESK[2] + 0.45]} center distanceFactor={7}><div className="o3-tag off_duty">总调度 · 已下班</div></Html>
              : undefined} />
          {people.map(({ b, desk }) => (
            <Workstation key={b.id} at={desk}
              glow={b.activity === "working" ? "#7dd3fc" : b.activity === "needs_you" ? "#fcd34d" : b.activity === "off_duty" ? null : "#cbd5e1"}
              label={b.activity === "off_duty" && !meeting ? (
                <Html zIndexRange={[10, 0]} position={[desk[0], 0.7, desk[2] + 0.45]} center distanceFactor={7}>
                  <div className="o3-tag off_duty" onClick={() => setSel(b.id)}>{b.name.split(" · ")[0]} · 已下班 · 今日 {b.done_today} 项</div>
                </Html>) : undefined} />
          ))}
          {people.filter((x: any) => !(x.gone && !meeting)).map(({ b, home, target, pose, face }) => (
            <Person key={b.id} bot={b} url={CHARS[hashIdx(b.id, CHARS.length)]} home={home} target={target} pose={pose} grid={grid}
              faceTo={face} bubble={bubbleOf(b)} onClick={() => setSel(b.id)}
              card={sel === b.id ? <PersonCard botId={b.id} k={k} go={go} onClose={() => setSel(null)} /> : undefined} />
          ))}
          {chief && chiefPlan && (
            <Person key={chief.id} bot={chief} url="/office/characters/character-male-f.glb" home={leadSeat} target={chiefPlan.target}
              pose={chiefPlan.pose} grid={grid} faceTo={chiefPlan.face} bubble={meeting ? "🎤 主持" : null} onClick={() => setSel(chief.id)}
              card={sel === chief.id ? <PersonCard botId={chief.id} k={k} go={go} onClose={() => setSel(null)} /> : undefined} />
          )}
          </group>
        </Suspense>
        <OrbitControls target={[0, 0.3, -0.3]} minPolarAngle={0.5} maxPolarAngle={1.25} minDistance={6} maxDistance={20} enablePan={false} />
      </Canvas>
    </div>
  );
}

useGLTF.preload(F("desk"));
CHARS.forEach((c) => useGLTF.preload(c));

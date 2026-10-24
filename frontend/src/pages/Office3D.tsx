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
const LOUNGE: V3[] = [[2.1, 0, -1.6], [2.7, 0, -1.2], [2.3, 0, -0.9], [3.0, 0, -1.8], [2.6, 0, -0.55], [3.2, 0, -0.9]];

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
      <Html position={[3.4, 1.45, -3.24]} transform distanceFactor={3.2} occlude={false}>
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
    <Html position={[-1.2, 0.98, -3.24]} transform distanceFactor={2.6} occlude={false}>
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
    <Html position={[-4.14, 0.92, 0.55]} rotation={[0, Math.PI / 2, 0]} transform distanceFactor={2.6} occlude={false}>
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

function Person({ bot, url, home, target, pose, faceTo, bubble, onClick }: {
  bot: Role; url: string; home: V3; target: V3; pose: "sit" | "idle" | "type"; faceTo: number; bubble: string | null; onClick: () => void;
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
    const to = new THREE.Vector3(...target);
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

// ---------- 主场景 ----------
export default function Office3D({ kpiId, go }: { kpiId: string; go: (p: string) => void }) {
  const { data: k } = usePoll<any>(`/kpis/${kpiId}`, 2500);
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
    if (b.activity === "off_duty") return { b, home: seat, target: [4.5, 0, 3.5] as V3, pose: "idle" as const, face: 0, desk, gone: true };
    const l = LOUNGE[i % LOUNGE.length];
    return { b, home: seat, target: l, pose: "idle" as const, face: hashIdx(b.id, 6), desk };
  });

  const counts = squad.reduce((a: any, b) => ({ ...a, [b.activity]: (a[b.activity] || 0) + 1 }), {});
  const bubbleOf = (b: Role) => b.activity === "needs_you" ? `❗ ${b.attention + b.approvals} 件事等你` : null;

  return (
    <div className="o3-wrap">
      <div className="o3-hud">
        <b>{k.market?.city || ""} {clock.data?.now?.slice(11, 16)}</b>
        {meeting ? <span className="pill info">{meeting}</span> : <span className="pill">{clock.data?.phase === "review" ? "下班时间" : "办公时间"}</span>}
        <span className="muted small">干活中 {counts.working || 0} · 待命 {counts.idle || 0} · 等你 {counts.needs_you || 0} · 下班 {counts.off_duty || 0}</span>
        <span className="muted small" style={{ marginLeft: "auto" }}>拖动旋转 · 滚轮缩放 · 点人物进工位</span>
      </div>
      <Canvas shadows camera={{ position: [8.5, 9.5, 11], fov: 30 }} dpr={[1, 2]}>
        <color attach="background" args={["#eef2f7"]} />
        <hemisphereLight args={["#ffffff", "#c9c2b6", 0.9]} />
        <directionalLight position={[6, 10, 4]} intensity={1.4} castShadow shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-9} shadow-camera-right={9} shadow-camera-top={9} shadow-camera-bottom={-9} />
        <Suspense fallback={<Html center><div className="muted">加载模型…</div></Html>}>
          <group scale={SCALE}>
          <Room market={k.market?.name || ""} />
          <KpiBoard k={k} />
          <ChannelScreen channel={k.channel} />
          {/* 总调度的主位 */}
          <Workstation at={LEAD_DESK} glow="#a5b4fc" label={
            <Html position={[LEAD_DESK[0], 0.85, LEAD_DESK[2] - 0.1]} center distanceFactor={7}><div className="o3-tag lead">总调度 · 主持</div></Html>} />
          {people.map(({ b, desk }) => (
            <Workstation key={b.id} at={desk}
              glow={b.activity === "working" ? "#7dd3fc" : b.activity === "needs_you" ? "#fcd34d" : b.activity === "off_duty" ? null : "#cbd5e1"}
              label={b.activity === "off_duty" && !meeting ? (
                <Html position={[desk[0], 0.7, desk[2] + 0.45]} center distanceFactor={7}>
                  <div className="o3-tag off_duty" onClick={() => go(`bot/${b.id}`)}>{b.name.split(" · ")[0]} · 已下班 · 今日 {b.done_today} 项</div>
                </Html>) : undefined} />
          ))}
          {people.filter((x: any) => !(x.gone && !meeting)).map(({ b, home, target, pose, face }) => (
            <Person key={b.id} bot={b} url={CHARS[hashIdx(b.id, CHARS.length)]} home={home} target={target} pose={pose}
              faceTo={face} bubble={bubbleOf(b)} onClick={() => go(b.current?.task_id ? `bot/${b.id}` : `bot/${b.id}`)} />
          ))}
          </group>
        </Suspense>
        <OrbitControls target={[0, 0.3, -0.3]} minPolarAngle={0.5} maxPolarAngle={1.25} minDistance={6} maxDistance={20} enablePan={false} />
      </Canvas>
    </div>
  );
}

useGLTF.preload(F("desk"));
CHARS.forEach((c) => useGLTF.preload(c));

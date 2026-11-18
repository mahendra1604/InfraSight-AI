import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Edges, Html, OrbitControls } from '@react-three/drei';
import { request } from '../../api';
import { useUi } from '../../ui';
import { useMotionSetting } from '../../motion';

const DAY = 86400000;
const WIDTH = 130;     // scene units for the whole schedule
const ROW = 3.4;

function Bar({ row, x0, length, z, reduced, hovered, onHover }) {
  const fill = useRef();
  const shown = useRef(reduced ? row.actual : 0);
  useFrame((_, delta) => {
    shown.current = reduced ? row.actual : shown.current + (row.actual - shown.current) * Math.min(1, delta * 1.6);
    const l = Math.max((shown.current / 100) * length, 0.01);
    fill.current.visible = l > 0.05;
    fill.current.scale.x = l;
    fill.current.position.x = x0 + l / 2;
  });
  const color = row.actual >= 100 ? '#4f8cff' : row.variance < 0 ? '#ffb547' : '#34d399';
  return <group onPointerOver={(e) => { e.stopPropagation(); onHover(row.id); }} onPointerOut={() => onHover(null)}>
    <mesh position={[x0 + length / 2, 1, z]}>
      <boxGeometry args={[length, 1.4, 2.2]}/>
      <meshStandardMaterial color={row.critical ? '#ffb547' : '#9fb7ff'} transparent opacity={hovered ? 0.3 : 0.14} depthWrite={false}/>
      <Edges color={row.critical ? '#ffb547' : hovered ? '#ffffff' : '#6d8fd8'}/>
    </mesh>
    <mesh ref={fill} position={[x0, 1, z]}>
      <boxGeometry args={[1, 1.5, 2.3]}/>
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={hovered ? 0.6 : 0.25}/>
    </mesh>
    <Html position={[x0 - 1.5, 1, z]} center distanceFactor={70} style={{ pointerEvents: 'none' }}><div className="sky-id">{row.id}</div></Html>
    {hovered && <Html position={[x0 + length / 2, 4.5, z]} center distanceFactor={60} style={{ pointerEvents: 'none' }}>
      <div className="three-tip"><b>{row.id}</b> {row.name}<br/>{row.startLabel} → {row.finishLabel} · {row.actual}% done of {row.planned}% planned{row.critical ? ' · critical' : ''}</div>
    </Html>}
  </group>;
}

function TodayPlane({ x, depth, reduced }) {
  const plane = useRef();
  useFrame((state) => { if (plane.current && !reduced) plane.current.material.opacity = 0.18 + 0.08 * Math.sin(state.clock.elapsedTime * 2); });
  return <group position={[x, 0, 0]}>
    <mesh ref={plane} position={[0, 4, 0]}>
      <boxGeometry args={[0.3, 8, depth]}/>
      <meshBasicMaterial color="#3be0ff" transparent opacity={0.22} depthWrite={false}/>
    </mesh>
    <Html position={[0, 9.5, -depth / 2]} center distanceFactor={70} style={{ pointerEvents: 'none' }}><div className="three-tip"><b>Today</b> (as of)</div></Html>
  </group>;
}

export default function Gantt3D({ paused = false, height = 440 }) {
  const { theme } = useUi();
  const { reduced } = useMotionSetting();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [hover, setHover] = useState(null);
  useEffect(() => {
    Promise.all([request('/activities'), request('/analysis/dependencies'), request('/project')])
      .then(([acts, dep, project]) => setData({ acts, dep, project })).catch((e) => setError(e.message));
  }, []);
  const model = useMemo(() => {
    if (!data) return null;
    const nodes = Object.fromEntries((data.dep.nodes || []).map((n) => [n.id, n]));
    const rows = data.acts.map((a) => {
      const start = new Date(a.planned_start || nodes[a.id]?.early_start || data.project.as_of);
      const finish = new Date(a.planned_finish || nodes[a.id]?.early_finish || start.getTime() + 10 * DAY);
      return { ...a, start, finish, critical: Boolean(nodes[a.id]?.critical) };
    }).filter((r) => !Number.isNaN(r.start.getTime())).sort((a, b) => a.start - b.start);
    if (!rows.length) return null;
    const t0 = Math.min(...rows.map((r) => r.start.getTime()));
    const t1 = Math.max(...rows.map((r) => r.finish.getTime() + DAY));
    const scale = WIDTH / Math.max((t1 - t0) / DAY, 1);
    const fmt = (d) => d.toLocaleDateString(undefined, { day: '2-digit', month: 'short' });
    const months = [];
    for (let d = new Date(new Date(t0).getFullYear(), new Date(t0).getMonth() + 1, 1); d.getTime() < t1; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) {
      months.push({ x: ((d.getTime() - t0) / DAY) * scale - WIDTH / 2, label: d.toLocaleDateString(undefined, { month: 'short', year: '2-digit' }) });
    }
    return {
      rows: rows.map((r, i) => ({ ...r, x0: ((r.start.getTime() - t0) / DAY) * scale - WIDTH / 2,
        length: Math.max(((r.finish - r.start) / DAY + 1) * scale, 0.6), z: (i - (rows.length - 1) / 2) * ROW,
        startLabel: fmt(r.start), finishLabel: fmt(r.finish) })),
      today: ((new Date(data.project.as_of).getTime() - t0) / DAY) * scale - WIDTH / 2,
      depth: rows.length * ROW + 4, months,
    };
  }, [data]);
  if (error) return <div className="error">{error}</div>;
  if (!data) return <div className="scene-loading">Building the 3D timeline…</div>;
  if (!model) return <div className="scene-loading">Import a schedule with start and finish dates to see the 3D timeline.</div>;
  const bg = theme === 'dark' ? '#0a1630' : '#e8eef8';
  return <div className="scene" style={{ height }}>
    <Canvas dpr={[1, 1.5]} frameloop={paused ? 'demand' : 'always'} camera={{ position: [-WIDTH * 0.2, WIDTH * 0.3, model.depth / 2 + WIDTH * 0.3], fov: 42 }}
      gl={{ antialias: true, preserveDrawingBuffer: true }}>
      <color attach="background" args={[bg]}/>
      <fog attach="fog" args={[bg, WIDTH * 1.2, WIDTH * 3]}/>
      <ambientLight intensity={0.65}/>
      <directionalLight position={[20, 60, 40]} intensity={1.1}/>
      <gridHelper args={[WIDTH * 1.6, 48, theme === 'dark' ? '#2c4f8f' : '#93abd9', theme === 'dark' ? '#1a2f5a' : '#c3d0e8']}/>
      {model.months.map((m) => <group key={m.label}>
        <mesh position={[m.x, 0.05, 0]}><boxGeometry args={[0.12, 0.05, model.depth]}/><meshBasicMaterial color="#6d8fd8" transparent opacity={0.6}/></mesh>
        <Html position={[m.x, 0.2, model.depth / 2 + 2.5]} center distanceFactor={80} style={{ pointerEvents: 'none' }}><div className="sky-id">{m.label}</div></Html>
      </group>)}
      {model.rows.map((r) => <Bar key={r.id} row={r} x0={r.x0} length={r.length} z={r.z} reduced={reduced} hovered={hover === r.id} onHover={setHover}/>)}
      <TodayPlane x={model.today} depth={model.depth} reduced={reduced}/>
      <OrbitControls makeDefault enableDamping target={[0, 0, 0]} maxPolarAngle={Math.PI / 2.1} minDistance={30} maxDistance={WIDTH * 2.5}/>
    </Canvas>
  </div>;
}

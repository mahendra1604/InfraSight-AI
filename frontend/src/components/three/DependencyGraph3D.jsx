import React, { useMemo, useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Html, OrbitControls, QuadraticBezierLine } from '@react-three/drei';
import { useUi } from '../../ui';

const COLORS = { complete: '#3569e8', behind: '#e58b28', ok: '#2f9e6b', critical: '#ff8a1f', red: '#d1493f', edge: '#9aa7bd' };
const STEP = 0.45;      // seconds between nodes when a delay spreads

function layout(nodes) {
  const columns = {};
  nodes.forEach((n) => { (columns[n.depth] ||= []).push(n); });
  const depths = Object.keys(columns).map(Number).sort((a, b) => a - b);
  const span = depths.length - 1;
  const pos = {};
  depths.forEach((d) => {
    const col = [...columns[d]].sort((a, b) => Number(b.critical) - Number(a.critical));
    col.forEach((n, i) => {
      const x = (d - span / 2) * 15;
      if (col.length === 1) {
        pos[n.id] = [x, Math.sin(d * 0.9) * 3, 0];
      } else {
        const angle = (i / col.length) * Math.PI * 2;
        const r = 7 + col.length * 1.2;
        pos[n.id] = [x, Math.sin(angle) * r * 0.9, Math.cos(angle) * r];
      }
    });
  });
  return pos;
}

function stateOf(n) {
  return n.actual >= 100 ? 'complete' : n.variance < 0 ? 'behind' : 'ok';
}

function Node({ n, p, selected, onSelect, impact, reveal, startRef, reduced }) {
  const mesh = useRef();
  const material = useRef();
  const ring = useRef();
  const radius = 1.3 + Math.min(n.downstream_count, 10) * 0.12;
  const isSource = impact?.activity_id === n.id;
  const shift = impact?.affected?.find((a) => a.id === n.id)?.shift_days;
  useFrame((state, delta) => {
    const t = state.clock.elapsedTime;
    const shown = reveal != null && startRef.current != null && t - startRef.current >= reveal;
    const red = isSource || (shown && shift != null);
    material.current.color.set(red ? COLORS.red : COLORS[stateOf(n)]);
    material.current.emissive.set(red ? COLORS.red : n.critical ? COLORS.critical : '#000000');
    material.current.emissiveIntensity = red && !reduced ? 0.4 + 0.35 * Math.sin(t * 5) : n.critical ? 0.18 : 0;
    const scale = selected ? 1.25 : red && !reduced ? 1 + 0.08 * Math.sin(t * 5) : 1;
    mesh.current.scale.setScalar(scale);
    if (ring.current && !reduced) ring.current.rotation.z += delta * 1.2;
  });
  const shownLabel = shift != null;
  return <group position={p}>
    <mesh ref={mesh} castShadow
      onClick={(event) => { event.stopPropagation(); onSelect?.(n.id); }}
      onPointerOver={(event) => { event.stopPropagation(); document.body.style.cursor = 'pointer'; }}
      onPointerOut={() => { document.body.style.cursor = ''; }}>
      <sphereGeometry args={[radius, 32, 32]}/>
      <meshStandardMaterial ref={material} roughness={0.35} metalness={0.15}/>
    </mesh>
    {n.critical && <mesh ref={ring} rotation={[Math.PI / 2.4, 0, 0]}>
      <torusGeometry args={[radius + 0.7, 0.12, 12, 64]}/>
      <meshStandardMaterial color={COLORS.critical} emissive={COLORS.critical} emissiveIntensity={0.6}/>
    </mesh>}
    <Html position={[0, radius + 1.6, 0]} center distanceFactor={70} zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
      <div className={`graph-tag ${selected ? 'selected' : ''} ${isSource ? 'source' : ''}`}>
        <b>{n.id}</b>{(selected || isSource || shownLabel) && <span>{n.name}</span>}
        {isSource && <em>+{impact.delay_days} d</em>}
        {!isSource && shownLabel && <em className="delay-label" style={{ animationDelay: `${reveal || 0}s` }}>+{shift} d</em>}
      </div>
    </Html>
  </group>;
}

function Edge({ from, to, critical, red, revealAt, startRef, reduced }) {
  const line = useRef();
  const mid = useMemo(() => [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2 + 3, (from[2] + to[2]) / 2], [from, to]);
  useFrame((state, delta) => {
    if (!line.current) return;
    const material = line.current.material;
    const shown = red && revealAt != null && startRef.current != null && state.clock.elapsedTime - startRef.current >= revealAt;
    material.color.set(shown ? COLORS.red : critical ? COLORS.critical : COLORS.edge);
    if (!reduced && (critical || shown)) material.dashOffset -= delta * (shown ? 6 : 2.5);
  });
  return <QuadraticBezierLine ref={line} start={from} end={to} mid={mid} lineWidth={critical ? 3 : 1.5} dashed={critical || red}
    dashSize={1.4} gapSize={0.7} color={critical ? COLORS.critical : COLORS.edge}/>;
}

function ImpactClock({ impact, startRef }) {
  const last = useRef(null);
  useFrame((state) => {
    if (impact !== last.current) {
      last.current = impact;
      startRef.current = impact ? state.clock.elapsedTime : null;
    }
  });
  return null;
}

export default function DependencyGraph3D({ data, impact, selected, onSelect, paused = false, reduced = false, height = 520 }) {
  const { theme } = useUi();
  const bg = theme === 'dark' ? '#081227' : '#e8eef8';
  const pos = useMemo(() => layout(data.nodes), [data]);
  const startRef = useRef(null);
  const order = useMemo(() => {
    if (!impact) return {};
    const reveal = { [impact.activity_id]: 0 };
    const topo = data.nodes.map((n) => n.id);
    impact.affected.map((a) => a.id).sort((a, b) => topo.indexOf(a) - topo.indexOf(b))
      .forEach((id, i) => { reveal[id] = reduced ? 0 : (i + 1) * STEP; });
    return reveal;
  }, [impact, data, reduced]);
  const span = Math.max(...data.nodes.map((n) => n.depth), 1);
  const distance = Math.max(40, span * 15 * 0.55 + 15);   // fit all columns (15 units apart) in a ~2:1 view
  return <div className="scene" style={{ height }}>
    <Canvas dpr={[1, 1.5]} frameloop={paused ? 'demand' : 'always'} camera={{ position: [0, distance * 0.3, distance], fov: 45 }}
      onPointerMissed={() => onSelect?.(null)} gl={{ antialias: true, preserveDrawingBuffer: true }}>
      <color attach="background" args={[bg]}/>
      <fog attach="fog" args={[bg, 60, 260]}/>
      <ambientLight intensity={0.55}/>
      <pointLight position={[0, 40, 40]} intensity={1800} distance={300}/>
      <directionalLight position={[-30, 30, 20]} intensity={0.8}/>
      <ImpactClock impact={impact} startRef={startRef}/>
      {data.edges.map((e) => {
        const red = Boolean(impact && order[e.from] != null && order[e.to] != null);
        return <Edge key={`${e.from}-${e.to}`} from={pos[e.from]} to={pos[e.to]} red={red} revealAt={red ? order[e.to] : null}
          critical={data.critical_path.includes(e.from) && data.critical_path.includes(e.to)} startRef={startRef} reduced={reduced}/>;
      })}
      {data.nodes.map((n) => <Node key={n.id} n={n} p={pos[n.id]} selected={selected === n.id} onSelect={onSelect} impact={impact}
        reveal={order[n.id]} startRef={startRef} reduced={reduced}/>)}
      <OrbitControls makeDefault enableDamping autoRotate={!paused && !reduced && !impact} autoRotateSpeed={0.35}
        minDistance={15} maxDistance={220}/>
    </Canvas>
  </div>;
}

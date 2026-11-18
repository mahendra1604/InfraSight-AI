import React, { useMemo, useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Edges, Html, OrbitControls } from '@react-three/drei';
import { useUi } from '../../ui';
import { useMotionSetting } from '../../motion';

const H = 22;   // tower height at 100 %
const COLORS = { complete: '#4f8cff', behind: '#ffb547', ahead: '#34d399', ok: '#34d399', risk: '#ff5d5d' };
const stateOf = (a) => (a.actual >= 100 ? 'complete' : a.variance < 0 ? 'behind' : a.variance > 0 ? 'ahead' : 'ok');

function Tower({ a, position, risk, reduced, hovered, onHover }) {
  const solid = useRef();
  const cap = useRef();
  const shown = useRef(reduced ? a.actual : 0);
  useFrame((state, delta) => {
    shown.current = reduced ? a.actual : shown.current + (a.actual - shown.current) * Math.min(1, delta * 1.8);
    const h = Math.max((shown.current / 100) * H, 0.05);
    solid.current.scale.y = h;
    solid.current.position.y = h / 2;
    if (cap.current) {
      cap.current.position.y = h + 0.25;
      cap.current.material.emissiveIntensity = reduced ? 0.6 : 0.5 + 0.4 * Math.sin(state.clock.elapsedTime * 4);
    }
  });
  const color = COLORS[stateOf(a)];
  return <group position={position}
    onPointerOver={(e) => { e.stopPropagation(); onHover(a.id); document.body.style.cursor = 'pointer'; }}
    onPointerOut={() => { onHover(null); document.body.style.cursor = ''; }}>
    <mesh position={[0, (a.planned / 100) * H / 2, 0]}>
      <boxGeometry args={[3.4, Math.max((a.planned / 100) * H, 0.05), 3.4]}/>
      <meshStandardMaterial color="#9fb7ff" transparent opacity={hovered ? 0.22 : 0.1} depthWrite={false}/>
      <Edges color={hovered ? '#ffffff' : '#6d8fd8'}/>
    </mesh>
    <mesh ref={solid} castShadow>
      <boxGeometry args={[2.8, 1, 2.8]}/>
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={hovered ? 0.55 : 0.18} roughness={0.4} metalness={0.2}/>
    </mesh>
    {risk === 'high' && <mesh ref={cap}><boxGeometry args={[3, 0.5, 3]}/><meshStandardMaterial color="#ff5d5d" emissive="#ff5d5d"/></mesh>}
    <Html position={[0, -1.6, 2]} center distanceFactor={60} style={{ pointerEvents: 'none' }}>
      <div className={`sky-id ${hovered ? 'on' : ''}`}>{a.id}</div>
    </Html>
    {hovered && <Html position={[0, Math.max(a.actual, a.planned) / 100 * H + 3, 0]} center distanceFactor={55} style={{ pointerEvents: 'none' }}>
      <div className="three-tip"><b>{a.id}</b> {a.name}<br/>{a.actual}% done · {a.planned}% planned{risk ? ` · ${risk} risk` : ''}
        {a.total_quantity ? <><br/>{Number(a.actual_quantity).toLocaleString()} / {Number(a.total_quantity).toLocaleString()} {a.unit}</> : null}</div>
    </Html>}
  </group>;
}

export default function Skyline3D({ activities, risks = {}, paused = false, height = 380 }) {
  const { theme } = useUi();
  const { reduced } = useMotionSetting();
  const [hover, setHover] = useState(null);
  const perRow = Math.min(9, Math.max(4, Math.ceil(Math.sqrt(activities.length * 2))));
  const layout = useMemo(() => activities.map((a, i) => {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    return [(col - (perRow - 1) / 2) * 6.5, 0, (row - (Math.ceil(activities.length / perRow) - 1) / 2) * 7.5];
  }), [activities, perRow]);
  const bg = theme === 'dark' ? '#0a1630' : '#e8eef8';
  const span = perRow * 6.5;
  return <div className="scene" style={{ height }}>
    <Canvas shadows dpr={[1, 1.5]} frameloop={paused ? 'demand' : 'always'} camera={{ position: [span * 0.42, span * 0.45, span * 0.72], fov: 40 }}
      gl={{ antialias: true, preserveDrawingBuffer: true }}>
      <color attach="background" args={[bg]}/>
      <fog attach="fog" args={[bg, span * 1.4, span * 3.2]}/>
      <ambientLight intensity={0.6}/>
      <directionalLight position={[30, 60, 25]} intensity={1.2} castShadow/>
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
        <planeGeometry args={[span * 4, span * 4]}/>
        <meshStandardMaterial color={theme === 'dark' ? '#0c1b3a' : '#dde5f2'}/>
      </mesh>
      <gridHelper args={[span * 2, 40, theme === 'dark' ? '#2c4f8f' : '#93abd9', theme === 'dark' ? '#1a2f5a' : '#c3d0e8']} position={[0, 0.02, 0]}/>
      {activities.map((a, i) => <Tower key={a.id} a={a} position={layout[i]} risk={risks[a.id]} reduced={reduced} hovered={hover === a.id} onHover={setHover}/>)}
      <OrbitControls makeDefault enableDamping autoRotate={!paused && !reduced && !hover} autoRotateSpeed={0.4} maxPolarAngle={Math.PI / 2.1}
        minDistance={span * 0.4} maxDistance={span * 2.6}/>
    </Canvas>
  </div>;
}

import React, { useRef, useState } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Html, OrbitControls } from '@react-three/drei';
import { useUi } from '../../ui';
import { useMotionSetting } from '../../motion';

const LEVEL = { high: '#ff5d5d', medium: '#ffb547', low: '#34d399' };
const H = 20;

function Pillar({ p, position, reduced, hovered, onHover }) {
  const mesh = useRef();
  const glow = useRef();
  const shown = useRef(reduced ? p.risk_score : 0);
  useFrame((state, delta) => {
    shown.current = reduced ? p.risk_score : shown.current + (p.risk_score - shown.current) * Math.min(1, delta * 1.7);
    const h = Math.max((shown.current / 100) * H, 0.08);
    mesh.current.scale.y = h;
    mesh.current.position.y = h / 2;
    if (glow.current) glow.current.material.opacity = p.risk_level === 'high' && !reduced ? 0.25 + 0.15 * Math.sin(state.clock.elapsedTime * 3) : 0.2;
  });
  const color = p.complete ? '#4f8cff' : LEVEL[p.risk_level] || '#8f9bb5';
  return <group position={position} onPointerOver={(e) => { e.stopPropagation(); onHover(p.activity_id); }} onPointerOut={() => onHover(null)}>
    <mesh ref={mesh}>
      <cylinderGeometry args={[1.5, 1.8, 1, 24]}/>
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={hovered ? 0.7 : 0.3} roughness={0.35}/>
    </mesh>
    <mesh ref={glow} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.05, 0]}>
      <ringGeometry args={[2.2, 3.2, 32]}/>
      <meshBasicMaterial color={color} transparent opacity={0.2}/>
    </mesh>
    <Html position={[0, (p.risk_score / 100) * H + 2.4, 0]} center distanceFactor={60} style={{ pointerEvents: 'none' }}>
      <div className={`sky-id ${hovered ? 'on' : ''}`}>{p.activity_id}{p.complete ? '' : ` · ${Math.round(p.delay_probability * 100)}%`}</div>
    </Html>
    {hovered && <Html position={[0, (p.risk_score / 100) * H + 6, 0]} center distanceFactor={55} style={{ pointerEvents: 'none' }}>
      <div className="three-tip"><b>{p.activity_id}</b> {p.name}<br/>Risk {p.risk_score} ({p.risk_level}) · {p.complete ? 'complete' : `${p.expected_delay_days} d expected delay`}
        {p.factors?.length ? <><br/>{p.factors.map((f) => f.label).join(' · ')}</> : null}</div>
    </Html>}
  </group>;
}

/** Activities on an arc, pillar height = risk score; ordered from highest risk. */
export default function RiskBars3D({ predictions, paused = false, height = 380 }) {
  const { theme } = useUi();
  const { reduced } = useMotionSetting();
  const [hover, setHover] = useState(null);
  const n = predictions.length;
  const radius = Math.max(22, n * 2.2);
  const bg = theme === 'dark' ? '#0a1630' : '#e8eef8';
  return <div className="scene" style={{ height }}>
    <Canvas dpr={[1, 1.5]} frameloop={paused ? 'demand' : 'always'} camera={{ position: [0, radius * 0.62, radius * 1.15], fov: 42 }}
      gl={{ antialias: true, preserveDrawingBuffer: true }}>
      <color attach="background" args={[bg]}/>
      <fog attach="fog" args={[bg, radius * 2, radius * 5]}/>
      <ambientLight intensity={0.6}/>
      <pointLight position={[0, 30, 0]} intensity={1500} distance={200} color={theme === 'dark' ? '#ff9c9c' : '#ffffff'}/>
      <directionalLight position={[20, 40, 30]} intensity={0.8}/>
      <gridHelper args={[radius * 4, 40, theme === 'dark' ? '#2c4f8f' : '#93abd9', theme === 'dark' ? '#1a2f5a' : '#c3d0e8']}/>
      {predictions.map((p, i) => {
        const angle = Math.PI * (0.15 + 0.7 * (n > 1 ? i / (n - 1) : 0.5));
        return <Pillar key={p.activity_id} p={p} reduced={reduced} hovered={hover === p.activity_id} onHover={setHover}
          position={[Math.cos(angle) * -radius, 0, Math.sin(angle) * -radius * 0.55 + radius * 0.25]}/>;
      })}
      <OrbitControls makeDefault enableDamping autoRotate={!paused && !reduced && !hover} autoRotateSpeed={0.3} maxPolarAngle={Math.PI / 2.1}
        minDistance={radius * 0.6} maxDistance={radius * 4}/>
    </Canvas>
  </div>;
}

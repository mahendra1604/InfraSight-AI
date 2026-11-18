import React, { useEffect, useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Grid, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { palette, SiteContent } from './SiteScene';

/** Camera per screen: [position], [look-at target]. Plot is ~120 x 80 m around the origin. */
const PRESETS = {
  Login: [[70, 48, 92], [0, 0, 0]],
  Overview: [[72, 52, 96], [0, 0, 0]],
  'Analyse reports': [[0, 62, 82], [0, 0, -6]],
  'Report review': [[-86, 34, 62], [0, 4, 0]],
  'Site plans': [[0.1, 150, 0.1], [0, 0, 0]],
  Activities: [[96, 22, 58], [0, 8, 0]],
  Dependencies: [[0, 82, 165], [0, 26, 0]],
  'Delay risk': [[64, 40, -92], [0, 0, 0]],
  Schedule: [[-132, 14, 6], [50, 8, 0]],
  'Audit trail': [[42, 112, 112], [0, 0, 0]],
};
const TOUR = ['Overview', 'Report review', 'Activities', 'Delay risk', 'Dependencies', 'Schedule', 'Site plans'];

function Ticker({ fps, active, deps }) {
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    invalidate();
    if (!active) return undefined;
    const id = setInterval(() => invalidate(), 1000 / fps);
    return () => clearInterval(id);
  }, [active, fps, invalidate, ...deps]);
  return null;
}

function CameraDirector({ preset, reduced, presentation }) {
  const { camera } = useThree();
  const target = useRef(new THREE.Vector3());
  const pos = useMemo(() => new THREE.Vector3(...preset[0]), [preset]);
  const look = useMemo(() => new THREE.Vector3(...preset[1]), [preset]);
  const first = useRef(true);
  useFrame((state, delta) => {
    if (presentation) return;  // OrbitControls drives the camera
    const angle = reduced ? 0 : state.clock.elapsedTime * 0.035;
    const orbit = pos.clone().sub(look).applyAxisAngle(new THREE.Vector3(0, 1, 0), angle).add(look);
    const k = reduced || first.current ? 1 : Math.min(1, delta * 1.2);
    camera.position.lerp(orbit, k);
    target.current.lerp(look, k);
    camera.lookAt(target.current);
    first.current = false;
  });
  return null;
}

function Particles({ count = 500, color, reduced }) {
  const points = useRef();
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      arr[i * 3] = (Math.random() - 0.5) * 320;
      arr[i * 3 + 1] = Math.random() * 90;
      arr[i * 3 + 2] = (Math.random() - 0.5) * 240;
    }
    return arr;
  }, [count]);
  useFrame((_, delta) => {
    if (reduced || !points.current) return;
    const p = points.current.geometry.attributes.position;
    for (let i = 0; i < count; i++) {
      p.array[i * 3 + 1] += delta * (1.5 + (i % 5) * 0.4);
      if (p.array[i * 3 + 1] > 90) p.array[i * 3 + 1] = 0;
    }
    p.needsUpdate = true;
  });
  return <points ref={points}>
    <bufferGeometry><bufferAttribute attach="attributes-position" args={[positions, 3]}/></bufferGeometry>
    <pointsMaterial color={color} size={0.7} sizeAttenuation transparent opacity={0.55} depthWrite={false}/>
  </points>;
}

function ScanBeam({ color, reduced }) {
  const beam = useRef();
  useFrame((state) => {
    if (!beam.current) return;
    beam.current.position.x = reduced ? 0 : Math.sin(state.clock.elapsedTime * 0.8) * 62;
  });
  return <mesh ref={beam} position={[0, 12, 0]}>
    <boxGeometry args={[1.2, 24, 86]}/>
    <meshBasicMaterial color={color} transparent opacity={0.22} depthWrite={false}/>
  </mesh>;
}

function TimeRings({ color, reduced }) {
  const group = useRef();
  useFrame((_, delta) => { if (group.current && !reduced) group.current.position.x = (group.current.position.x - delta * 6) % 30; });
  return <group ref={group}>
    {Array.from({ length: 12 }, (_, i) => <mesh key={i} position={[-150 + i * 30, 10, 0]} rotation={[0, Math.PI / 2, 0]}>
      <torusGeometry args={[34, 0.18, 8, 96]}/>
      <meshBasicMaterial color={color} transparent opacity={0.35}/>
    </mesh>)}
  </group>;
}

function Constellation({ color, reduced }) {
  const group = useRef();
  const { nodes, lines } = useMemo(() => {
    const n = Array.from({ length: 26 }, (_, i) => new THREE.Vector3(-90 + (i % 9) * 22 + Math.sin(i) * 6, 28 + Math.cos(i * 1.7) * 9, Math.sin(i * 2.3) * 26));
    const l = [];
    n.forEach((p, i) => { if (i + 1 < n.length && (i + 1) % 9) l.push([p, n[i + 1]]); if (i + 9 < n.length) l.push([p, n[i + 9]]); });
    return { nodes: n, lines: l };
  }, []);
  const geometry = useMemo(() => new THREE.BufferGeometry().setFromPoints(lines.flat()), [lines]);
  useFrame((_, delta) => { if (group.current && !reduced) group.current.rotation.y += delta * 0.04; });
  return <group ref={group}>
    <lineSegments geometry={geometry}><lineBasicMaterial color={color} transparent opacity={0.35}/></lineSegments>
    {nodes.map((p, i) => <mesh key={i} position={p}><sphereGeometry args={[1.1, 12, 12]}/><meshBasicMaterial color={i % 4 === 0 ? '#ffb547' : color}/></mesh>)}
  </group>;
}

export default function BackgroundWorld({ model, tab = 'Overview', theme = 'dark', reduced = false, paused = false, focusIds = [], presentation = false, freeCamera = false }) {
  const pal = palette(theme, true);
  const accent = theme === 'dark' ? '#3be0ff' : '#3569e8';
  const [tourIndex, setTourIndex] = React.useState(0);
  useEffect(() => {
    if (!presentation || reduced || freeCamera) return undefined;
    const id = setInterval(() => setTourIndex((i) => (i + 1) % TOUR.length), 7000);
    return () => clearInterval(id);
  }, [presentation, reduced, freeCamera]);
  const preset = PRESETS[presentation ? TOUR[tourIndex] : tab] || PRESETS.Overview;
  const risk = tab === 'Delay risk' && !presentation;
  const animate = !paused && !reduced;
  return <div className="world" aria-hidden={!presentation}>
    <Canvas dpr={[0.75, 1]} frameloop="demand" camera={{ position: preset[0], fov: 42, near: 1, far: 3000 }} gl={{ antialias: true, powerPreference: 'low-power' }}>
      <Ticker fps={presentation ? 45 : 30} active={animate || presentation} deps={[tab, theme, model, focusIds.join()]}/>
      <color attach="background" args={[pal.bg]}/>
      <fog attach="fog" args={[risk ? (theme === 'dark' ? '#1d0b12' : '#f6e3e3') : pal.bg, 110, 420]}/>
      <ambientLight intensity={theme === 'dark' ? 0.5 : 0.8}/>
      <directionalLight position={[60, 120, 40]} intensity={theme === 'dark' ? 0.9 : 1.1}/>
      {risk && <pointLight position={[0, 40, 0]} color="#ff5d5d" intensity={2500} distance={260}/>}
      <Grid position={[0, -0.6, 0]} infiniteGrid cellSize={6} sectionSize={30} cellColor={pal.cell} sectionColor={pal.section}
        fadeDistance={360} fadeStrength={1.4}/>
      {model && <SiteContent model={model} ambient theme={theme} reduced={reduced} highlightIds={focusIds} labels={presentation}/>}
      <Particles color={accent} reduced={!animate}/>
      {(tab === 'Analyse reports' && !presentation) && <ScanBeam color={accent} reduced={!animate}/>}
      {(tab === 'Schedule' && !presentation) && <TimeRings color={accent} reduced={!animate}/>}
      {((tab === 'Dependencies' && !presentation) || (presentation && TOUR[tourIndex] === 'Dependencies')) && <Constellation color={accent} reduced={!animate}/>}
      <CameraDirector preset={preset} reduced={reduced} presentation={presentation && freeCamera}/>
      {presentation && freeCamera && <OrbitControls enableDamping makeDefault maxPolarAngle={Math.PI / 2.1} minDistance={30} maxDistance={400}/>}
    </Canvas>
  </div>;
}

import React, { useMemo, useRef } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Edges, Grid, Html, Line, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';

export const STATUS_COLORS = { complete: '#4f8cff', on_plan: '#34d399', behind: '#ffb547', unlinked: '#8f9bb5' };
const RISK_RED = '#ff5d5d';
const FOUNDATION = 0.6;

/** Colours per theme and per use (panel = data view, ambient = blueprint backdrop). */
export function palette(theme, ambient) {
  if (ambient) {
    return theme === 'light'
      ? { bg: '#e9eef7', ground: '#e1e8f4', plot: '#d6e0f2', earth: '#d6e0f2', prepared: '#d6e0f2', cell: '#bccbe6', section: '#93abd9',
          boundary: '#4f7dff', body: '#4f7dff', glass: '#9fb7ff', edge: '#5b82e0', ghost: '#c2d2f0', built: '#4f7dff', flat: '#b5c7ea' }
      : { bg: '#050c1b', ground: '#06101f', plot: '#0e1d3c', earth: '#0e1d3c', prepared: '#0e1d3c', cell: '#1d355f', section: '#2f5296',
          boundary: '#6f97ff', body: '#4f7dff', glass: '#8fb0ff', edge: '#8fb0ff', ghost: '#1b2f58', built: '#4f7dff', flat: '#1f3a72' };
  }
  return theme === 'light'
    ? { bg: '#e8eef8', ground: '#dfe6f1', earth: '#cdbb9c', prepared: '#d7e8dc', cell: '#b8c2d3', section: '#8f9cb3', boundary: '#2b3b5c',
        glass: '#a9c0f0', edge: '#7f97c4', ghostRoad: '#d5d9e0', ghostDrain: '#bcd3f5', road: '#46505f', drain: '#2f6fd6' }
    : { bg: '#0a1630', ground: '#0b1834', earth: '#3a3124', prepared: '#173a33', cell: '#1c3360', section: '#2c4f8f', boundary: '#7aa2ff',
        glass: '#7fa6ff', edge: '#6d8fd8', ghostRoad: '#2a3b5c', ghostDrain: '#1d3766', road: '#9fb0cc', drain: '#3b9cff' };
}

export const colorFor = (e) => STATUS_COLORS[e.status] || STATUS_COLORS.unlinked;

function useEased(target, reduced, speed = 1.5) {
  const value = useRef(reduced ? target : 0);
  return (delta) => {
    value.current = reduced ? target : value.current + (target - value.current) * Math.min(1, delta * speed);
    return value.current;
  };
}

function Tag({ e, position, selected }) {
  return <Html position={position} center distanceFactor={95} zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
    <div className={`scene-tag ${selected ? 'selected' : ''} ${e.risk_level === 'high' ? 'risk' : ''}`}>
      <strong>{e.name}</strong>{e.progress != null ? ` · ${Math.round(e.progress)}%` : ''}
    </div>
  </Html>;
}

function Pickable({ e, onSelect, children }) {
  if (!onSelect) return <group>{children}</group>;
  return <group
    onClick={(event) => { event.stopPropagation(); onSelect(e.id); }}
    onPointerOver={(event) => { event.stopPropagation(); document.body.style.cursor = 'pointer'; }}
    onPointerOut={() => { document.body.style.cursor = ''; }}>{children}</group>;
}

function Building({ e, selected, highlighted, onSelect, reduced, ambient, pal, labels }) {
  const floors = e.floors || 3;
  const floorHeight = (e.height || floors * 3.5) / floors;
  const built = useRef();
  const material = useRef();
  const ease = useEased((e.progress ?? 0) / 100, reduced);
  const high = e.risk_level === 'high';
  useFrame((state, delta) => {
    const h = Math.max(ease(delta) * floors * floorHeight, 0.001);
    built.current.scale.y = h;
    built.current.position.y = FOUNDATION + h / 2;
    if (material.current) {
      const t = state.clock.elapsedTime;
      material.current.emissiveIntensity = highlighted ? 0.6 + 0.4 * Math.sin(t * 4)
        : high && !reduced && !ambient ? 0.25 + 0.2 * Math.sin(t * 3) : selected ? 0.25 : ambient ? 0.35 : 0;
    }
  });
  const color = ambient ? (highlighted ? '#3be0ff' : pal.body) : colorFor(e);
  const emissive = highlighted ? '#3be0ff' : ambient ? pal.body : high ? RISK_RED : color;
  return <Pickable e={e} onSelect={onSelect}>
    <group position={[e.position[0], 0, e.position[1]]}>
      <mesh position={[0, FOUNDATION / 2, 0]} receiveShadow castShadow>
        <boxGeometry args={[e.length + 1.6, FOUNDATION, e.width + 1.6]}/>
        <meshStandardMaterial color={ambient ? pal.flat : '#9aa6bb'} transparent={ambient} opacity={ambient ? 0.6 : 1}/>
      </mesh>
      <mesh ref={built} castShadow>
        <boxGeometry args={[e.length, 1, e.width]}/>
        <meshStandardMaterial ref={material} color={color} emissive={emissive} emissiveIntensity={0}
          roughness={0.5} metalness={0.15} transparent={ambient} opacity={ambient ? (highlighted ? 0.85 : 0.45) : 1}/>
      </mesh>
      {Array.from({ length: floors }, (_, i) => <mesh key={i} position={[0, FOUNDATION + floorHeight * (i + 0.5), 0]}>
        <boxGeometry args={[e.length, floorHeight, e.width]}/>
        <meshStandardMaterial color={pal.glass} transparent opacity={ambient ? 0.05 : 0.1} depthWrite={false}/>
        <Edges color={highlighted ? '#3be0ff' : selected ? '#ffffff' : pal.edge}/>
      </mesh>)}
      {labels && <Tag e={e} selected={selected} position={[0, FOUNDATION + floors * floorHeight + 4, 0]}/>}
    </group>
  </Pickable>;
}

function segmentsOf(path) {
  let start = 0;
  return path.slice(1).map((p, i) => {
    const a = path[i];
    const length = Math.hypot(p[0] - a[0], p[1] - a[1]);
    const seg = { a, b: p, length, start, angle: -Math.atan2(p[1] - a[1], p[0] - a[0]) };
    start += length;
    return seg;
  });
}

/** Roads and drains: a ghost of the full route plus a built part that grows along it. */
function Route({ e, selected, highlighted, onSelect, reduced, ambient, pal, labels }) {
  const segments = useMemo(() => segmentsOf(e.path || []), [e.path]);
  const total = segments.reduce((sum, s) => sum + s.length, 0);
  const refs = useRef([]);
  const ease = useEased((e.progress ?? 0) / 100, reduced);
  const road = e.kind === 'road';
  const width = road ? 6 : e.kind === 'drain' ? 1.3 : 1;
  const height = road ? 0.25 : 0.7;
  const builtColor = highlighted ? '#3be0ff' : ambient ? pal.built : e.risk_level === 'high' ? RISK_RED : road ? pal.road : e.kind === 'drain' ? pal.drain : '#a08cff';
  useFrame((state, delta) => {
    const done = ease(delta) * total;
    segments.forEach((s, i) => {
      const mesh = refs.current[i];
      if (!mesh) return;
      const l = Math.max(Math.min(done - s.start, s.length), 0.001);
      const dx = (s.b[0] - s.a[0]) / s.length;
      const dz = (s.b[1] - s.a[1]) / s.length;
      mesh.visible = l > 0.05;
      mesh.scale.x = l;
      mesh.position.set(s.a[0] + dx * l / 2, height / 2 + 0.05, s.a[1] + dz * l / 2);
    });
  });
  if (!segments.length) return null;
  const mid = segments[Math.floor(segments.length / 2)];
  const ghost = ambient ? pal.ghost : road ? pal.ghostRoad : pal.ghostDrain;
  return <Pickable e={e} onSelect={onSelect}>
    {segments.map((s, i) => <group key={i}>
      <mesh position={[(s.a[0] + s.b[0]) / 2, height / 2, (s.a[1] + s.b[1]) / 2]} rotation={[0, s.angle, 0]} receiveShadow>
        <boxGeometry args={[s.length, height * 0.9, width]}/>
        <meshStandardMaterial color={ghost} transparent opacity={selected ? 0.85 : 0.5}/>
      </mesh>
      <mesh ref={(m) => { refs.current[i] = m; }} rotation={[0, s.angle, 0]} castShadow>
        <boxGeometry args={[1, height, width * 0.86]}/>
        <meshStandardMaterial color={builtColor} emissive={ambient || highlighted ? builtColor : '#000'} emissiveIntensity={ambient || highlighted ? 0.5 : 0} roughness={0.7}/>
      </mesh>
    </group>)}
    {labels && <Tag e={e} selected={selected} position={[(mid.a[0] + mid.b[0]) / 2, 4, (mid.a[1] + mid.b[1]) / 2]}/>}
  </Pickable>;
}

function FlatArea({ e, selected, highlighted, onSelect, ambient, pal, labels }) {
  const color = highlighted ? '#3be0ff' : ambient ? pal.flat : e.status === 'unlinked' ? '#8f9bb5' : colorFor(e);
  const stripes = e.kind === 'parking' ? Math.max(2, Math.floor(e.length / 3)) : 0;
  return <Pickable e={e} onSelect={onSelect}>
    <group position={[e.position[0], 0, e.position[1]]}>
      <mesh position={[0, 0.15, 0]} receiveShadow>
        <boxGeometry args={[e.length, 0.3, e.width]}/>
        <meshStandardMaterial color={color} transparent opacity={selected || highlighted ? 0.95 : 0.72}/>
        {(selected || highlighted) && <Edges color={highlighted ? '#3be0ff' : '#ffffff'}/>}
      </mesh>
      {Array.from({ length: stripes }, (_, i) => <mesh key={i} position={[-e.length / 2 + (i + 0.5) * (e.length / stripes), 0.32, -e.width / 4]}>
        <boxGeometry args={[0.15, 0.02, e.width / 2]}/>
        <meshStandardMaterial color="#ffffff" transparent opacity={0.8}/>
      </mesh>)}
      {labels && <Tag e={e} selected={selected} position={[0, 3, 0]}/>}
    </group>
  </Pickable>;
}

function Plot({ plot, element, pal }) {
  const prepared = (element?.progress ?? 0) / 100;
  const color = useMemo(() => new THREE.Color(pal.earth).lerp(new THREE.Color(pal.prepared), prepared), [prepared, pal]);
  const hl = plot.length / 2;
  const hw = plot.width / 2;
  return <group>
    <mesh position={[0, -0.25, 0]} receiveShadow>
      <boxGeometry args={[plot.length, 0.5, plot.width]}/>
      <meshStandardMaterial color={color}/>
    </mesh>
    <Grid position={[0, 0.01, 0]} args={[plot.length, plot.width]} cellSize={5} sectionSize={20} cellColor={pal.cell}
      sectionColor={pal.section} fadeDistance={400} infiniteGrid={false}/>
    <Line points={[[-hl, 0.05, -hw], [hl, 0.05, -hw], [hl, 0.05, hw], [-hl, 0.05, hw], [-hl, 0.05, -hw]]} color={pal.boundary}
      lineWidth={2} dashed dashSize={3} gapSize={1.5}/>
  </group>;
}

/** Plot + elements, without camera, lights or controls (shared by panel, background world and login). */
export function SiteContent({ model, selectedId, onSelect, reduced = false, ambient = false, theme = 'dark', highlightIds = [], labels = !ambient }) {
  const pal = palette(theme, ambient);
  const plotElement = model.elements.find((e) => e.kind === 'plot');
  return <group>
    <Plot plot={model.plot} element={plotElement} pal={pal}/>
    {model.elements.map((e) => {
      if (e.kind === 'plot') return null;
      const highlighted = highlightIds.length > 0 && (e.activities || []).some((a) => highlightIds.includes(a.id));
      const props = { e, selected: e.id === selectedId, highlighted, onSelect, reduced, ambient, pal, labels };
      if (e.kind === 'building') return <Building key={e.id} {...props}/>;
      if (e.path) return <Route key={e.id} {...props}/>;
      if (e.position) return <FlatArea key={e.id} {...props}/>;
      return null;
    })}
  </group>;
}

const viewPosition = (plot) => [plot.length * 0.62, plot.length * 0.55, plot.width * 1.25];

function CameraRig({ plot, reduced }) {
  const { camera } = useThree();
  const target = useMemo(() => new THREE.Vector3(...viewPosition(plot)), [plot]);
  const done = useRef(reduced);
  useFrame((_, delta) => {
    if (done.current) return;
    camera.position.lerp(target, Math.min(1, delta * 1.4));
    camera.lookAt(0, 0, 0);
    if (camera.position.distanceTo(target) < 0.5) done.current = true;
  });
  return null;
}

/** Interactive data view (Overview, Site plans). */
export function SiteScene({ model, selectedId, onSelect, paused = false, reduced = false, theme = 'dark', height = 460 }) {
  const { plot } = model;
  const pal = palette(theme, false);
  const start = reduced ? viewPosition(plot) : [plot.length * 1.1, plot.length * 0.95, plot.width * 1.8];
  return <div className="scene" style={{ height }}>
    <Canvas shadows dpr={[1, 1.5]} frameloop={paused ? 'demand' : 'always'} camera={{ position: start, fov: 40, near: 1, far: 2000 }}
      onPointerMissed={() => onSelect?.(null)} gl={{ antialias: true, preserveDrawingBuffer: true }}>
      <color attach="background" args={[pal.bg]}/>
      <fog attach="fog" args={[pal.bg, plot.length * 1.6, plot.length * 3.6]}/>
      <ambientLight intensity={theme === 'dark' ? 0.55 : 0.75}/>
      <hemisphereLight args={['#ffffff', theme === 'dark' ? '#1b2b4f' : '#b8c4d8', 0.5]}/>
      <directionalLight position={[plot.length * 0.6, plot.length, plot.width * 0.5]} intensity={1.3} castShadow
        shadow-mapSize={[1024, 1024]} shadow-camera-left={-plot.length} shadow-camera-right={plot.length}
        shadow-camera-top={plot.length} shadow-camera-bottom={-plot.length}/>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.51, 0]} receiveShadow>
        <planeGeometry args={[plot.length * 30, plot.width * 30]}/>
        <meshStandardMaterial color={pal.ground}/>
      </mesh>
      <SiteContent model={model} selectedId={selectedId} onSelect={onSelect} reduced={reduced} theme={theme}/>
      <CameraRig plot={plot} reduced={reduced}/>
      <OrbitControls makeDefault enableDamping autoRotate={!paused && !reduced} autoRotateSpeed={0.5} maxPolarAngle={Math.PI / 2.15}
        minDistance={plot.length * 0.4} maxDistance={plot.length * 3}/>
    </Canvas>
  </div>;
}

/** A fixed demo layout for the login screen (no project data before sign-in). */
export const DEMO_SITE = {
  plot: { length: 120, width: 80 },
  elements: [
    { id: 'plot', kind: 'plot', name: 'Plot', progress: 100 },
    { id: 'a', kind: 'building', name: 'Block A', length: 30, width: 20, floors: 3, height: 10.5, position: [-28, -12], progress: 70, status: 'on_plan' },
    { id: 'b', kind: 'building', name: 'Block B', length: 25, width: 20, floors: 3, height: 10.5, position: [12, -12], progress: 35, status: 'on_plan' },
    { id: 'p', kind: 'parking', name: 'Parking', length: 40, width: 15, position: [-25, 20], progress: 0, status: 'unlinked' },
    { id: 'r', kind: 'road', name: 'Road', path: [[-60, 34], [54, 34], [54, -36]], progress: 60, status: 'on_plan' },
    { id: 'd', kind: 'drain', name: 'Drain', path: [[58, 38], [58, -38], [-58, -38]], progress: 80, status: 'on_plan' },
  ],
};

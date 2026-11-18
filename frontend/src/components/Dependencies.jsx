import React, { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { motion } from 'motion/react';
import { GitBranch, Zap } from 'lucide-react';
import { fmtDate, request } from '../api';
import { AnimatedNumber, FadeIn, staggerChild, staggerParent, useMotionSetting } from '../motion';
import { TiltCard } from '../ui';

const DependencyGraph3D = lazy(() => import('./three/DependencyGraph3D'));

const NODE_W = 168;
const NODE_H = 58;
const GAP_X = 70;
const GAP_Y = 22;

function layout(nodes) {
  const columns = {};
  nodes.forEach((n) => { (columns[n.depth] ||= []).push(n); });
  const depths = Object.keys(columns).map(Number).sort((a, b) => a - b);
  const pos = {};
  depths.forEach((d, ci) => columns[d].forEach((n, ri) => { pos[n.id] = { x: 10 + ci * (NODE_W + GAP_X), y: 10 + ri * (NODE_H + GAP_Y) }; }));
  const width = 20 + depths.length * (NODE_W + GAP_X) - GAP_X;
  const height = 20 + Math.max(...depths.map((d) => columns[d].length)) * (NODE_H + GAP_Y) - GAP_Y;
  return { pos, width, height };
}

function Graph({ data, highlight }) {
  const { pos, width, height } = useMemo(() => layout(data.nodes), [data]);
  const affected = new Set(highlight?.affected?.map((a) => a.id) || []);
  return <div className="graph-scroll">
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label="Activity dependency graph">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" className="edge-head"/></marker></defs>
      {data.edges.map((e) => {
        const a = pos[e.from]; const b = pos[e.to];
        const critical = data.critical_path.includes(e.from) && data.critical_path.includes(e.to);
        const x1 = a.x + NODE_W; const y1 = a.y + NODE_H / 2; const x2 = b.x; const y2 = b.y + NODE_H / 2;
        return <path key={`${e.from}-${e.to}`} d={`M${x1},${y1} C${x1 + GAP_X / 2},${y1} ${x2 - GAP_X / 2},${y2} ${x2 - 2},${y2}`}
          className={`edge ${critical ? 'critical' : ''}`} markerEnd="url(#arrow)"/>;
      })}
      {data.nodes.map((n) => {
        const p = pos[n.id];
        const state = n.actual >= 100 ? 'complete' : n.variance < 0 ? 'behind' : 'ok';
        return <g key={n.id} transform={`translate(${p.x},${p.y})`} className={`gnode ${state} ${n.critical ? 'critical' : ''} ${highlight?.activity_id === n.id ? 'source' : ''} ${affected.has(n.id) ? 'affected' : ''}`}>
          <title>{`${n.id} ${n.name}\n${n.actual}% of ${n.planned}% planned\nFloat ${n.total_float_days} d`}</title>
          <rect width={NODE_W} height={NODE_H} rx="7"/>
          <text x="10" y="20" className="gid">{n.id}{n.critical ? ' · critical' : ''}</text>
          <text x="10" y="38" className="gname">{n.name.length > 22 ? `${n.name.slice(0, 21)}…` : n.name}</text>
          <rect x="10" y="46" width={NODE_W - 20} height="4" rx="2" className="gtrack"/>
          <rect x="10" y="46" width={(NODE_W - 20) * Math.min(n.actual, 100) / 100} height="4" rx="2" className="gbar"/>
        </g>;
      })}
    </svg>
  </div>;
}

export function DependenciesPage({ paused = false }) {
  const { reduced } = useMotionSetting();
  const [view, setView] = useState('3d');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState('');
  const [days, setDays] = useState(5);
  const [impact, setImpact] = useState(null);

  useEffect(() => {
    request('/analysis/dependencies').then((d) => {
      setData(d);
      const firstBehind = d.nodes?.find((n) => n.variance < 0 && n.actual < 100);
      setSelected(firstBehind?.id || d.nodes?.[0]?.id || '');
      // Shareable what-if link: #/dependencies?simulate=A103&days=7
      const params = new URLSearchParams(window.location.hash.split('?')[1] || '');
      const target = params.get('simulate');
      if (target && d.nodes?.some((n) => n.id === target)) {
        const delay = Number(params.get('days')) || 5;
        setSelected(target);
        setDays(delay);
        simulate(target, delay);
      }
    }).catch((e) => setError(e.message));
  }, []);

  async function simulate(id = selected, delay = days) {
    try {
      setImpact(await request(`/analysis/impact/${encodeURIComponent(id)}?delay_days=${delay}`));
      setError('');
      window.history.replaceState(null, '', `#/dependencies?simulate=${encodeURIComponent(id)}&days=${delay}`);
    } catch (e) { setError(e.message); }
  }

  if (error && !data) return <div className="error">{error}</div>;
  if (!data) return <div className="panel">Calculating the critical path…</div>;
  if (!data.valid) return <div className="error">{data.message}</div>;
  if (!data.nodes.length) return <div className="panel">The schedule has no activities yet. Import one on the Schedule screen.</div>;
  return <>
    <motion.section className="stats" variants={staggerParent} initial="hidden" animate="show">
      {[['Project duration', data.project_duration_days, ' d', `${fmtDate(data.project_start)} → ${fmtDate(data.project_finish)}`],
        ['Critical activities', data.critical_path.length, '', 'Zero total float'],
        ['Dependencies', data.edges.length, '', 'Finish-to-start links'],
        ['Bottlenecks', data.bottlenecks.length, '', 'Unfinished, with many successors']].map(([label, value, suffix, caption]) =>
        <TiltCard key={label} variants={staggerChild}><div>{label}<GitBranch size={16}/></div>
          <strong><AnimatedNumber value={value} suffix={suffix}/></strong><small>{caption}</small></TiltCard>)}
    </motion.section>
    <FadeIn as="section" className="panel" delay={0.1}>
      <div className="section-title"><h2>Dependency graph</h2>
        <div className="graph-toolbar">
          <span className="legend"><i className="lg-critical"/>Critical <i className="lg-behind"/>Behind plan <i className="lg-done"/>Complete</span>
          <div className="segmented">{['3d', '2d'].map((v) => <button key={v} className={view === v ? 'active' : ''} onClick={() => setView(v)}>{v.toUpperCase()}</button>)}</div>
        </div>
      </div>
      {view === '3d'
        ? <Suspense fallback={<div className="scene-loading">Loading 3D graph…</div>}>
            <DependencyGraph3D data={data} impact={impact} selected={selected} paused={paused} reduced={reduced}
              onSelect={(id) => { if (id) { setSelected(id); setImpact(null); } }}/>
            <p className="footnote">Drag to orbit, scroll to zoom, click an activity to select it for the simulation. Orange links are the critical path; a simulated slip spreads in red.</p>
          </Suspense>
        : <Graph data={data} highlight={impact}/>}
      <p className="footnote">{data.method}</p>
    </FadeIn>
    <div className="overview-grid">
      <section className="panel">
        <h2>Critical path & float</h2>
        <div className="table-scroll"><table><thead><tr><th>Activity</th><th>Duration</th><th>Early start</th><th>Early finish</th><th>Float</th><th>Downstream</th></tr></thead>
          <tbody>{data.nodes.map((n) => <tr key={n.id} className={n.critical ? 'critical-row' : ''}>
            <td><small className="activity-id">{n.id}</small>{n.name}</td><td>{n.duration_days} d</td><td>{fmtDate(n.early_start)}</td>
            <td>{fmtDate(n.early_finish)}</td><td>{n.critical ? <span className="pill warning">critical</span> : `${n.total_float_days} d`}</td><td>{n.downstream_count}</td>
          </tr>)}</tbody></table></div>
      </section>
      <section className="panel">
        <span className="eyebrow">WHAT IF IT SLIPS?</span>
        <h2>Downstream impact</h2>
        <div className="impact-form">
          <label>Activity<select value={selected} onChange={(e) => { setSelected(e.target.value); setImpact(null); }}>
            {data.nodes.filter((n) => n.actual < 100).map((n) => <option key={n.id} value={n.id}>{n.id} · {n.name}</option>)}
          </select></label>
          <label>Delay (days)<input type="number" min="0" max="365" value={days} onChange={(e) => setDays(e.target.value)}/></label>
          <button className="primary" onClick={() => simulate()}><Zap size={15}/> Simulate</button>
        </div>
        {impact && <motion.div className="impact-result" key={`${impact.activity_id}-${impact.delay_days}`} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
          <p><strong>{impact.explanation}</strong></p>
          {impact.affected.length ? <ul>{impact.affected.map((a, i) => <motion.li key={a.id} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
            transition={{ delay: reduced ? 0 : (i + 1) * 0.45 }}><span className="node-id">{a.id}</span> {a.name} <b>+{a.shift_days} d</b></motion.li>)}</ul>
            : <p className="muted">No successor moves: the delay is absorbed by float.</p>}
        </motion.div>}
        <h3 className="sub">Bottlenecks</h3>
        {data.bottlenecks.length ? <ul className="bottlenecks">{data.bottlenecks.map((b) => <li key={b.id}><span className="node-id">{b.id}</span>{b.name}<small>{b.downstream_count} downstream{b.critical ? ' · critical' : ''}{b.variance < 0 ? ` · ${b.variance} pp` : ''}</small></li>)}</ul> : <p className="muted">None.</p>}
      </section>
    </div>
  </>;
}

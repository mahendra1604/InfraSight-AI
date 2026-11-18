import React, { useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { Box, Info, PauseCircle } from 'lucide-react';
import { request } from '../../api';
import { useMotionSetting } from '../../motion';
import { useUi } from '../../ui';
import { SiteScene, STATUS_COLORS } from './SiteScene';

const KIND_LABEL = { building: 'Building', road: 'Road', drain: 'Drain', parking: 'Parking', utility: 'Utility', plot: 'Plot', area: 'Area' };
const STATUS_LABEL = { complete: 'Complete', on_plan: 'On plan', behind: 'Behind plan', unlinked: 'No linked activities' };

function Details({ element }) {
  if (!element) {
    return <div className="scene-details empty"><Info size={18}/><p>Click a building, road or drain to see its activities, progress and delay risk. Drag to orbit, scroll to zoom.</p></div>;
  }
  const q = element.quantity;
  return <motion.div className="scene-details" key={element.id} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }}>
    <span className="eyebrow">{KIND_LABEL[element.kind] || element.kind}</span>
    <h3>{element.name}</h3>
    <div className="scene-stats">
      <div><small>Progress</small><strong>{element.progress != null ? `${element.progress}%` : '—'}</strong></div>
      <div><small>Planned</small><strong>{element.planned != null ? `${element.planned}%` : '—'}</strong></div>
      <div><small>Risk</small><strong className={element.risk_level === 'high' ? 'risk-text' : ''}>{element.risk_level || '—'}</strong></div>
    </div>
    <p className="muted"><span className="dot-status" style={{ background: STATUS_COLORS[element.status] }}/>{STATUS_LABEL[element.status] || element.status}
      {q ? ` · ${Number(q.total).toLocaleString()} ${q.unit}` : ''}{element.floors ? ` · ${element.floors} floors` : ''}</p>
    {element.activities?.length ? <ul className="scene-acts">{element.activities.map((a) => <li key={a.id}>
      <span><b>{a.id}</b> {a.name}</span>
      <div className="mini-track"><motion.i initial={{ width: 0 }} animate={{ width: `${a.actual}%` }} transition={{ duration: 0.8 }}/>
        <em style={{ left: `${a.planned}%` }} title={`Planned ${a.planned}%`}/></div>
      <small>{a.actual}% of {a.planned}% planned{a.risk_level ? ` · ${a.risk_level} risk` : ''}</small>
    </li>)}</ul> : <p className="muted">No schedule activity is linked to this element yet (name it e.g. “{element.name} …”).</p>}
  </motion.div>;
}

export default function SiteModelPanel({ paused, refreshKey, planJob, height = 460 }) {
  const { reduced } = useMotionSetting();
  const { theme } = useUi();
  const [model, setModel] = useState(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
  useEffect(() => { request(`/site-model${planJob ? `?plan_job=${planJob}` : ''}`).then(setModel).catch((e) => setError(e.message)); }, [refreshKey, planJob]);
  if (error) return <div className="error">{error}</div>;
  if (!model) return <div className="scene-loading"><Box className="spin-slow" size={22}/> Building the 3D site…</div>;
  const element = model.elements.find((e) => e.id === selected);
  return <div className="site-model">
    <div className="scene-wrap">
      <SiteScene model={model} selectedId={selected} onSelect={setSelected} paused={paused} reduced={reduced} theme={theme} height={height}/>
      <AnimatePresence>{paused && <motion.div className="scene-paused" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
        <PauseCircle size={14}/> 3D paused while the AI is working</motion.div>}</AnimatePresence>
      <div className="scene-legend">
        {Object.entries(STATUS_COLORS).map(([k, c]) => <span key={k}><i style={{ background: c }}/>{STATUS_LABEL[k]}</span>)}
        <span><i className="pulse-dot"/>High delay risk</span>
      </div>
    </div>
    <Details element={element}/>
    <p className="footnote scene-note">{model.source.note}{model.source.label ? ` Source: ${model.source.label}.` : ''} {model.linking}
      {model.unlinked_activities.length > 0 && ` Not on the model: ${model.unlinked_activities.map((a) => a.id).join(', ')}.`}</p>
  </div>;
}

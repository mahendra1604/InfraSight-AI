import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, CheckCircle2, Download, Loader2, Map as MapIcon, RefreshCw, Ruler, XCircle } from 'lucide-react';
import { can, download, post, postForm, request } from '../api';
import { elapsed } from './Analyse';

const SiteModelPanel = lazy(() => import('./three/SiteModelPanel'));

const PLAN_TYPES = '.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.pdf';
const MODES = [
  ['vision', 'Vision + OCR', 'Reads the drawing image and uses OCR for small text. Best for layouts (≈ 2–3 min).'],
  ['text', 'OCR + text model', 'Faster, but the layout is lost, so labels may be assigned to the wrong element.'],
];
const STEPS = ['Reading the drawing', 'Reading labels', 'Calculating the take-off', 'Finished'];
const fmt = (n) => (n == null || n === '' ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }));

function stepIndex(job) {
  if (job.status === 'done') return 3;
  const s = job.stage || '';
  if (s.startsWith('Calculating')) return 2;
  if (s.startsWith('Reading the drawing with') || s.startsWith('Reading labels')) return 1;
  return job.status === 'running' ? 0 : -1;
}

function TakeoffRow({ total, activities, user, jobId, filename, onApplied }) {
  const options = [...(total.candidates || []).map((c) => c.activity_id), ...activities.map((a) => a.id)]
    .filter((id, i, all) => id && all.indexOf(id) === i);
  const [target, setTarget] = useState(total.point_marker ? '' : total.suggestion?.activity_id || '');
  const [amount, setAmount] = useState(total.total);
  const [state, setState] = useState('');
  const activity = activities.find((a) => a.id === target);
  const edited = Number(amount) !== total.total;

  async function apply() {
    setState('saving');
    try {
      await request(`/activities/${encodeURIComponent(target)}/quantity`, post({
        total_quantity: Number(amount), unit: total.unit,
        source: `Site plan ${filename} (analysis #${jobId})${edited ? `, corrected from ${total.total} ${total.unit}` : ''}` }));
      setState('done');
      onApplied();
    } catch (e) {
      setState(e.message);
    }
  }

  return <tr className={total.needs_review ? 'flag-row' : ''}>
    <td><strong>{total.name}</strong><small className="muted block">{total.labels.join(' · ')}</small></td>
    <td>{total.category}</td>
    <td className="num"><strong>{fmt(total.total)}</strong> {total.unit || <span className="pill warning">no unit</span>}<small className="muted block">{total.measure}{total.parts > 1 ? ` · ${total.parts} labels summed` : ''}</small></td>
    <td>
      <select value={target} onChange={(e) => { setTarget(e.target.value); setState(''); }} disabled={!can(user, 'manager')}>
        <option value="">— not in schedule —</option>
        {options.map((id) => {
          const c = (total.candidates || []).find((x) => x.activity_id === id);
          const a = activities.find((x) => x.id === id);
          return <option key={id} value={id}>{id} · {a?.name || c?.name}{c ? ` (${Math.round(c.score * 100)}%)` : ''}</option>;
        })}
      </select>
      {activity && <small className="muted block">Now: {activity.total_quantity ? `${fmt(activity.total_quantity)} ${activity.unit}` : 'no quantity'}</small>}
    </td>
    <td>{can(user, 'manager') ? <div className="apply-cell">
      <input type="number" min="0" step="any" value={amount} aria-label={`Quantity for ${total.name}`} onChange={(e) => { setAmount(e.target.value); setState(''); }}/>
      <button disabled={!target || !total.unit || !(Number(amount) > 0) || state === 'saving'} onClick={apply}>
        {state === 'done' ? <><Check size={14}/> Applied</> : edited ? 'Apply corrected' : 'Apply'}</button></div> : <small className="muted">manager only</small>}
      {state && !['saving', 'done'].includes(state) && <small className="error-text block">{state}</small>}
    </td>
  </tr>;
}

function PlanResult({ job, activities, user, onApplied }) {
  const r = job.result;
  const [notice, setNotice] = useState('');
  const totals = r.takeoff.totals;
  return <>
    {r.plan.answer && <div className="model-answer"><span className="eyebrow">MODEL RESPONSE</span><p>{r.plan.answer}</p></div>}
    <div className="analysis-meta">
      {r.plan.drawing_title && <span>{r.plan.drawing_title}</span>}
      {r.plan.scale && <span>Scale {r.plan.scale}</span>}
      {r.plan.drawing_date && <span>{r.plan.drawing_date}</span>}
      <span>{r.plan.elements.length} labels read</span>
    </div>
    <h3 className="sub">Plan → 3D</h3>
    <Suspense fallback={<div className="scene-loading">Extruding the plan into 3D…</div>}>
      <SiteModelPanel planJob={job.id} refreshKey={job.id} height={360}/>
    </Suspense>
    <h3 className="sub">Quantity take-off ({totals.length})</h3>
    <p className="footnote">{r.takeoff.method} Rows with a coloured edge need checking. Applying a quantity lets engineers report progress in {totals[0]?.unit || 'units'} instead of %.</p>
    {totals.length ? <div className="table-scroll"><table className="takeoff"><thead><tr><th>Element</th><th>Category</th><th>Total</th><th>Schedule activity</th><th/></tr></thead>
      <tbody>{totals.map((t, i) => <TakeoffRow key={`${t.name}-${t.measure}-${i}`} total={t} activities={activities} user={user} jobId={job.id}
        filename={job.filename} onApplied={() => { setNotice(`Quantity saved for ${t.name}. Engineers can now report it by quantity.`); onApplied(); }}/>)}</tbody></table></div>
      : <p className="muted">No printed dimensions were found on this drawing.</p>}
    {notice && <p className="notice">{notice}</p>}
    {(r.takeoff.notes || []).length > 0 && <div className="warning-list">{r.takeoff.notes.map((n, i) => <span key={i}><AlertTriangle size={12}/> {n}</span>)}</div>}
    {r.takeoff.other_dimensions.length > 0 && <p className="muted">Other dimensions (not summed): {r.takeoff.other_dimensions.map((o) => `${o.name} ${o.dimension} ${fmt(o.value)} ${o.unit || ''}`).join(' · ')}</p>}
    <details className="raw-labels"><summary>Labels the model read ({r.plan.elements.length})</summary>
      <ul>{r.plan.elements.map((e, i) => <li key={i}><code>{e.label_text || '—'}</code> → {e.name} · {e.dimension_type} {fmt(e.value)}{e.value2 != null ? ` × ${fmt(e.value2)}` : ''} {e.unit || ''}{e.needs_review ? ' · check' : ''}</li>)}</ul>
    </details>
    <h3 className="sub">Draft schedule ({r.draft_schedule.length} activities)</h3>
    <p className="footnote">A rule-based first draft: site preparation → drains/utilities and building foundations → superstructure, roads and parking after the drains. The AI cannot know durations or dates. Download it, fill them in Excel, then import it on the Schedule screen with “Keep existing activities” ticked.</p>
    <div className="table-scroll"><table><thead><tr><th>ID</th><th>Activity</th><th>After</th><th>Quantity</th></tr></thead>
      <tbody>{r.draft_schedule.map((row) => <tr key={row.activity_id}><td>{row.activity_id}</td><td>{row.name}</td><td>{row.predecessor || '—'}</td><td>{fmt(row.total_quantity)} {row.unit}</td></tr>)}</tbody></table></div>
    <div className="actions">
      <button className="primary" onClick={() => download(`/plans/${job.id}/draft-schedule.csv`, `draft-schedule-plan-${job.id}.csv`)}><Download size={15}/> Draft schedule (CSV)</button>
      <button onClick={() => download(`/plans/${job.id}/takeoff.csv`, `takeoff-plan-${job.id}.csv`)}><Download size={15}/> Take-off (CSV)</button>
    </div>
    {r.document.warnings.length > 0 && <div className="warning-list">{r.document.warnings.map((w, i) => <span key={i}><AlertTriangle size={12}/> {w}</span>)}</div>}
  </>;
}

function PlanTracker({ jobId, activities, user, onChanged }) {
  const [job, setJob] = useState(null);
  const [now, setNow] = useState(new Date());
  const finished = useRef(false);
  useEffect(() => {
    finished.current = false;
    let timer;
    async function poll() {
      try {
        const data = await request(`/jobs/${jobId}`);
        setJob(data);
        if (data.status === 'done' || data.status === 'failed') { if (!finished.current) { finished.current = true; onChanged(); } return; }
      } catch { /* keep polling */ }
      timer = setTimeout(poll, 2000);
    }
    poll();
    const tick = setInterval(() => setNow(new Date()), 1000);
    return () => { clearTimeout(timer); clearInterval(tick); };
  }, [jobId]);
  if (!job) return <section className="panel job"><Loader2 className="spin" size={18}/> Loading…</section>;
  const step = stepIndex(job);
  return <section className={`panel job ${job.status}`} aria-live="polite">
    <div className="section-title"><div><span className="eyebrow">SITE PLAN #{job.id}</span><h2>{job.filename}</h2></div>
      <span className={`pill ${job.status === 'failed' ? 'warning' : ''}`}>{['queued', 'running'].includes(job.status) && <Loader2 className="spin" size={12}/>} {job.status}</span></div>
    <ol className="steps">{STEPS.map((label, i) => <li key={label} className={i < step || job.status === 'done' ? 'done' : i === step ? 'current' : ''}>
      {i < step || job.status === 'done' ? <CheckCircle2 size={14}/> : i === step ? <Loader2 className="spin" size={14}/> : <span className="step-dot"/>}{label}</li>)}</ol>
    <div className="analysis-meta">{job.stage && job.status !== 'done' && <span>{job.stage}</span>}<span>{elapsed(job, now)}</span>{job.model && <span>{job.model}</span>}</div>
    {job.status === 'failed' && <div className="error" role="alert"><XCircle size={16}/> {job.error}</div>}
    {job.status === 'done' && <PlanResult job={job} activities={activities} user={user} onApplied={onChanged}/>}
  </section>;
}

export function SitePlansPage({ user, data, onChanged }) {
  const [file, setFile] = useState(null);
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState('vision');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [active, setActive] = useState(null);
  const [jobs, setJobs] = useState([]);
  const loadJobs = () => request('/jobs?purpose=plan').then((list) => { setJobs(list); return list; }).catch(() => []);
  useEffect(() => { loadJobs().then((list) => { if (list.length) setActive((current) => current ?? list[0].id); }); }, []);

  async function submit(force = false) {
    setBusy(true); setError(''); setMessage('');
    try {
      const job = await postForm('/plans/upload', { file, instruction: question, mode, force: force ? 'true' : undefined });
      setActive(job.id);
      if (job.duplicate) setMessage(job.message);
      loadJobs();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return <>
    <section className="panel">
      <span className="eyebrow">SITE PLAN → QUANTITIES → SCHEDULE</span>
      <h2>Read a site plan or layout drawing</h2>
      <p className="muted">The model reads every <strong>printed</strong> dimension (lengths, “30 m x 20 m” sizes, areas, counts) and which element it belongs to. It never measures the drawing: totals, areas and unit conversions are calculated in code. Use the result to set activity quantities, so site engineers can report progress as “180 m laid” instead of a percentage, and to start a draft schedule.</p>
      {!can(user, 'engineer') ? <p className="muted">Your role ({user.role}) can view results but not upload drawings.</p> : <div className="upload-form">
        <label className="dropzone"><MapIcon size={22}/>
          <span>{file ? <><strong>{file.name}</strong> · {(file.size / 1024).toFixed(0)} KB</> : 'Choose a site plan image or PDF (export .dwg drawings to PDF/PNG first)'}</span>
          <input type="file" accept={PLAN_TYPES} onChange={(e) => { setFile(e.target.files[0] || null); setMessage(''); }}/>
        </label>
        <label className="field">Question (optional)
          <textarea rows="2" maxLength={2000} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Example: What is the total drain length and the road width?"/>
        </label>
        <fieldset className="modes"><legend>How should the drawing be read?</legend>
          {MODES.map(([value, label, hint]) => <label key={value} className={mode === value ? 'active' : ''}>
            <input type="radio" name="plan-mode" value={value} checked={mode === value} onChange={() => setMode(value)}/>
            <span><strong>{label}</strong><small>{hint}</small></span></label>)}
        </fieldset>
        <div className="actions">
          <button className="primary" disabled={!file || busy} onClick={() => submit(false)}><Ruler size={16}/> {busy ? 'Uploading…' : 'Read drawing'}</button>
          {message && <button onClick={() => submit(true)} disabled={busy}><RefreshCw size={14}/> Read again anyway</button>}
        </div>
        {message && <p className="notice">{message}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <p className="footnote">Tips: crop large A1/A0 sheets to the area you need; labels must be printed on the drawing; a sample is in data/samples/site_plan.png.</p>
      </div>}
    </section>
    {active && <PlanTracker jobId={active} activities={data.activities} user={user} onChanged={() => { onChanged(); loadJobs(); }}/>}
    <section className="panel">
      <div className="section-title"><h2>Previous drawings</h2><button onClick={loadJobs}><RefreshCw size={14}/> Refresh</button></div>
      {jobs.length ? <div className="table-scroll"><table><thead><tr><th>#</th><th>File</th><th>Status</th><th>Mode</th><th>Time</th><th/></tr></thead>
        <tbody>{jobs.map((j) => <tr key={j.id}><td>{j.id}</td><td>{j.filename}</td><td><span className={`pill ${j.status === 'failed' ? 'warning' : ''}`}>{j.status}</span></td>
          <td>{j.method || j.mode}</td><td>{j.duration_s != null ? `${j.duration_s} s` : j.stage || '—'}</td><td><button onClick={() => setActive(j.id)}>Open</button></td></tr>)}</tbody></table></div>
        : <p className="muted">No drawings read yet.</p>}
    </section>
  </>;
}

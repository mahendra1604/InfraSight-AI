import React, { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { AlertTriangle, Bot, Check, X } from 'lucide-react';
import { can, post, request } from '../api';
import { useUi } from '../ui';
import { MatchPill } from './Analyse';

function ReportCard({ report, activities, user, busy, run }) {
  const { setFocus } = useUi();
  const [activityId, setActivityId] = useState(report.activity_id);
  const [progress, setProgress] = useState(report.progress);
  const [note, setNote] = useState('');
  const activity = activities.find((a) => a.id === report.activity_id);
  const pending = report.status === 'pending';
  const ai = Boolean(report.job_id);
  const candidates = report.candidates || [];
  const options = [...candidates.map((c) => c.activity_id), ...activities.map((a) => a.id)].filter((id, i, all) => all.indexOf(id) === i);

  function decide(action) {
    const body = { action };
    if (activityId !== report.activity_id) body.activity_id = activityId;
    if (Number(progress) !== report.progress) body.progress = Number(progress);
    if (note.trim()) body.note = note.trim();
    run(() => request(`/reports/${report.id}/review`, post(body)), action === 'approve' ? 'Progress approved and recorded in the audit trail.' : 'Report rejected.');
  }

  return <motion.article layout className={`report ${report.needs_review ? 'flagged' : ''}`} initial={{ opacity: 0, y: 14 }}
    animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: 180, y: -120, scale: 0.4, rotate: 8, transition: { duration: 0.55, ease: [0.4, 0, 0.2, 1] } }}
    transition={{ duration: 0.35 }} onMouseEnter={() => setFocus([report.activity_id])} onMouseLeave={() => setFocus(null)}
    whileHover={{ y: -2 }} title="Hovering highlights this activity's building in the 3D world">
    <div className="section-title">
      <span className="activity-id">REPORT {report.id} · {report.reporting_date}{report.evidence_page ? ` · page ${report.evidence_page}` : ''}</span>
      <span className="report-pills">{ai && <span className="pill"><Bot size={11}/> AI</span>}<MatchPill status={report.match_status} score={report.match_score}/><span className="pill">{report.status}</span></span>
    </div>
    <blockquote>{report.text}</blockquote>
    {ai && report.extracted_activity && <p className="muted">Report says “{report.extracted_activity}” → matched to <strong>{report.activity_id} · {activity?.name}</strong>{report.match_method ? ` (${report.match_method})` : ''}</p>}
    {!ai && <p><strong>{report.activity_id}</strong> · {activity?.name}</p>}
    {report.issue && <p className="issue"><AlertTriangle size={13}/> {report.issue}</p>}
    <p className="muted">Cumulative progress: <strong>{report.progress}%</strong>
      {report.quantity_done != null && <> = <strong>{Number(report.quantity_done).toLocaleString()} {report.quantity_unit}</strong>{activity?.total_quantity ? ` of ${Number(activity.total_quantity).toLocaleString()} ${activity.unit}` : ''}</>}
      {' '}(currently {activity?.actual ?? '—'}% approved) · {report.source}{report.model ? ` · ${report.model}` : ''}</p>
    {report.warnings?.length > 0 && <div className="warning-list">{report.warnings.map((w, i) => <span key={i}>{w}</span>)}</div>}
    {pending && can(user, 'manager') && <div className="review-controls">
      <label>Activity
        <select value={activityId} onChange={(e) => setActivityId(e.target.value)}>
          {options.map((id) => {
            const c = candidates.find((x) => x.activity_id === id);
            const a = activities.find((x) => x.id === id);
            return <option key={id} value={id}>{id} · {a?.name || c?.name}{c ? ` (${Math.round(c.score * 100)}%)` : ''}</option>;
          })}
        </select>
      </label>
      <label>Progress %<input type="number" min="0" max="100" step="0.1" value={progress} onChange={(e) => setProgress(e.target.value)}/></label>
      <label className="grow">Note (optional)<input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="e.g. verified on site"/></label>
      <div className="actions">
        <button disabled={busy} className="primary" onClick={() => decide('approve')}><Check size={16}/>{activityId !== report.activity_id || Number(progress) !== report.progress ? 'Correct & approve' : 'Approve progress'}</button>
        <button disabled={busy} onClick={() => decide('reject')}><X size={16}/>Reject</button>
      </div>
    </div>}
    {pending && !can(user, 'manager') && <p className="footnote">A project manager must approve this report.</p>}
  </motion.article>;
}

function ManualReportForm({ data, busy, run }) {
  const [activityId, setActivityId] = useState(data.activities[0]?.id || '');
  const [measure, setMeasure] = useState('percent');
  const activity = data.activities.find((a) => a.id === activityId);
  const byQuantity = measure === 'quantity' && activity?.total_quantity;
  return <form onSubmit={(e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    const value = Number(f.get('value'));
    run(async () => {
      await request('/reports', post({ activity_id: activityId, text: f.get('text'), reporting_date: f.get('reporting_date'),
        ...(byQuantity ? { quantity_done: value } : { progress: value }) }));
      form.reset();
    }, 'Report added to the review queue.');
  }}>
    <label>Activity<select value={activityId} onChange={(e) => setActivityId(e.target.value)}>
      {data.activities.map((a) => <option key={a.id} value={a.id}>{a.id} · {a.name}{a.total_quantity ? ` (${Number(a.total_quantity).toLocaleString()} ${a.unit})` : ''}</option>)}</select></label>
    <label>Reporting date<input name="reporting_date" type="date" required defaultValue={data.project.as_of}/></label>
    <div className="segmented measure">
      <button type="button" className={measure === 'percent' ? 'active' : ''} onClick={() => setMeasure('percent')}>By %</button>
      <button type="button" className={measure === 'quantity' ? 'active' : ''} onClick={() => setMeasure('quantity')} disabled={!activity?.total_quantity}
        title={activity?.total_quantity ? '' : 'Set a total quantity for this activity first (Site plans or Schedule import)'}>By quantity</button>
    </div>
    {byQuantity
      ? <label>Cumulative quantity done ({activity.unit} of {Number(activity.total_quantity).toLocaleString()})<input key="qty" name="value" type="number" min="0" step="any" required/></label>
      : <label>Cumulative progress (%)<input key="pct" name="value" type="number" min="0" max="100" step="0.1" required/></label>}
    <label>Site update<textarea name="text" required maxLength="5000" rows="4"/></label>
    <button className="primary" disabled={busy}>Submit for review</button>
  </form>;
}

export function ReviewPage({ data, user, busy, run }) {
  const [filter, setFilter] = useState('pending');
  const shown = data.reports.filter((r) => filter === 'all' || r.status === filter);
  return <div className="review-grid">
    <section className="panel">
      <div className="section-title">
        <h2>Site reports</h2>
        <div className="segmented">{['pending', 'approved', 'rejected', 'all'].map((f) => <button key={f} className={filter === f ? 'active' : ''} onClick={() => setFilter(f)}>{f} ({f === 'all' ? data.reports.length : data.reports.filter((r) => r.status === f).length})</button>)}</div>
      </div>
      <p className="muted">AI suggestions show the model, how the activity was matched and the similarity score. Correct the activity or progress before approving when needed; every decision is audited.</p>
      <AnimatePresence mode="popLayout" initial={false}>
        {shown.map((r) => <ReportCard key={r.id} report={r} activities={data.activities} user={user} busy={busy} run={run}/>)}
      </AnimatePresence>
      {!shown.length && <p className="muted">No {filter} reports.</p>}
    </section>
    <section className="panel">
      <h2>Add a manual report</h2>
      {!can(user, 'engineer') ? <p className="muted">Your role cannot add reports.</p> : <ManualReportForm data={data} busy={busy} run={run}/>}
    </section>
  </div>;
}

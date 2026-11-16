import React, { useEffect, useState } from 'react';
import { CalendarRange, Upload } from 'lucide-react';
import { can, fmtDate, postForm, request } from '../api';

export function SchedulePage({ user, onChanged }) {
  const [file, setFile] = useState(null);
  const [validation, setValidation] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [versions, setVersions] = useState([]);
  const [keep, setKeep] = useState(false);

  const loadVersions = () => request('/schedules/versions').then(setVersions).catch(() => {});
  useEffect(() => { loadVersions(); }, []);

  async function act(path, setter) {
    setBusy(true); setError('');
    try { setter(await postForm(path, { file, keep_missing: keep ? 'true' : 'false' })); } catch (e) { setError(e.message); setValidation(null); } finally { setBusy(false); }
  }

  return <div className="review-grid">
    <section className="panel">
      <span className="eyebrow">SCHEDULE MANAGEMENT</span>
      <h2>Import a project schedule</h2>
      <p className="muted">CSV (UTF-8) or Excel exported from Primavera P6 / MS Project. Required: activity ID, name, planned %, actual %. Optional: predecessors (separate with “;”, FS links such as “A101FS+2” are read as A101), start, finish, duration, WBS, total quantity + unit (enables reporting progress by quantity). Common export headers such as “Task Name” and “% Complete” are mapped automatically.</p>
      <label className="dropzone"><CalendarRange size={22}/>
        <span>{file ? <strong>{file.name}</strong> : 'Choose a .csv or .xlsx schedule'}</span>
        <input type="file" accept=".csv,.xlsx,.xlsm" onChange={(e) => { setFile(e.target.files[0] || null); setValidation(null); setResult(null); }}/>
      </label>
      <label className="check"><input type="checkbox" checked={keep} onChange={(e) => { setKeep(e.target.checked); setValidation(null); }}/>
        <span><strong>Keep existing activities</strong> (merge). Tick this when adding a draft schedule from a site plan; untick to replace the baseline.</span></label>
      <div className="actions">
        <button disabled={!file || busy} onClick={() => act('/schedules/validate', setValidation)}>Validate</button>
        {can(user, 'manager') && <button className="primary" disabled={!validation || busy} onClick={() => act('/schedules/import', (r) => { setResult(r); loadVersions(); onChanged(); })}><Upload size={15}/> Import as baseline</button>}
      </div>
      {validation && <p className="notice">{validation.message} {validation.activities} activities, {validation.links} dependency links, {validation.with_dates} with dates, {validation.with_quantities} with quantities.</p>}
      {result && <p className="notice">{result.message}{result.kept_with_reports?.length ? ` Kept (referenced by reports): ${result.kept_with_reports.join(', ')}.` : ''}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <p className="footnote">Import never lowers approved actual progress. Sample files: data/samples/schedule.csv and data/samples/schedule_extended.csv.</p>
    </section>
    <section className="panel">
      <h2>Baseline versions</h2>
      {versions.length ? versions.map((v) => <div className="audit-row" key={v.id}><CalendarRange size={18}/><div><strong>Version {v.id} · {v.filename}</strong>
        <p>{v.activities} activities ({v.added} added, {v.updated} updated, {v.removed} removed) · {v.imported_by} · {fmtDate(v.imported_at)}</p></div></div>)
        : <p className="muted">The seeded demo baseline is in use. Imports appear here.</p>}
    </section>
  </div>;
}

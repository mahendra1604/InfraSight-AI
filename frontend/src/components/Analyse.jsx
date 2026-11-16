import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronRight, FileSearch, Loader2, RefreshCw, Upload, XCircle } from 'lucide-react';
import { can, postForm, request } from '../api';

const REPORT_TYPES = '.txt,.md,.docx,.pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp';
const MODES = [
  ['auto', 'Automatic', 'Text layer or OCR first, vision model only when needed (fastest)'],
  ['text', 'Text / OCR only', 'Never use the vision model'],
  ['vision', 'Vision model', 'Send pages/images straight to Qwen3-VL (slower, best for photos and plans)'],
];
const STEPS = ['Reading the document', 'Extracting', 'Matching activities', 'Finished'];
const MATCH_LABEL = { id_match: 'ID match', suggested: 'Suggested match', needs_mapping: 'Needs mapping', manual: 'Manual' };

export function elapsed(job, now) {
  const start = job.started_at || job.created_at;
  if (!start) return '';
  const end = job.finished_at ? new Date(job.finished_at) : now;
  const seconds = Math.max(0, Math.round((end - new Date(start)) / 1000));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} min ${seconds % 60} s` : `${seconds} s`;
}

function stepIndex(job) {
  if (job.status === 'done') return 3;
  const stage = job.stage || '';
  if (stage.startsWith('Matching')) return 2;
  if (stage.startsWith('Extracting') || stage.startsWith('Reading page')) return 1;
  return job.status === 'running' ? 0 : -1;
}

export function MatchPill({ status, score }) {
  if (!status) return null;
  const warn = status === 'needs_mapping' || status === 'unmatched';
  return <span className={`pill ${warn ? 'warning' : ''}`} title={score != null ? `Similarity ${score}` : ''}>
    {MATCH_LABEL[status] || status}{score != null && status !== 'manual' ? ` · ${Math.round(score * 100)}%` : ''}
  </span>;
}

export function JobTracker({ jobId, onFinished, onReview }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [now, setNow] = useState(new Date());
  const finished = useRef(false);

  useEffect(() => {
    finished.current = false;
    let timer;
    async function poll() {
      try {
        const data = await request(`/jobs/${jobId}`);
        setJob(data);
        setError('');
        if (data.status === 'done' || data.status === 'failed') {
          if (!finished.current) { finished.current = true; onFinished?.(data); }
          return;
        }
      } catch (e) {
        setError(e.message);
      }
      timer = setTimeout(poll, 2000);
    }
    poll();
    const tick = setInterval(() => setNow(new Date()), 1000);
    return () => { clearTimeout(timer); clearInterval(tick); };
  }, [jobId]);

  if (!job) return <section className="panel job"><Loader2 className="spin" size={18}/> Loading analysis {jobId}… {error}</section>;
  const step = stepIndex(job);
  const result = job.result || {};
  const extraction = result.extraction || {};
  return <section className={`panel job ${job.status}`} aria-live="polite">
    <div className="section-title">
      <div><span className="eyebrow">ANALYSIS #{job.id}</span><h2>{job.filename}</h2></div>
      <span className={`pill ${job.status === 'failed' ? 'warning' : ''}`}>
        {job.status === 'running' || job.status === 'queued' ? <Loader2 className="spin" size={12}/> : null} {job.status}
      </span>
    </div>
    <ol className="steps">
      {STEPS.map((label, i) => <li key={label} className={i < step || job.status === 'done' ? 'done' : i === step ? 'current' : ''}>
        {i < step || job.status === 'done' ? <CheckCircle2 size={14}/> : i === step ? <Loader2 className="spin" size={14}/> : <span className="step-dot"/>}
        {label}
      </li>)}
    </ol>
    <div className="analysis-meta">
      {job.stage && job.status !== 'done' && <span>{job.stage}</span>}
      <span>{elapsed(job, now)}</span>
      {job.method && <span>{job.method === 'ocr' ? 'OCR + text model' : job.method}</span>}
      {job.model && <span>{job.model}</span>}
      {job.instruction && <span title={job.instruction}>Question asked</span>}
    </div>
    {job.status === 'queued' && <p className="muted">Waiting for the model. Jobs run one at a time on this computer.</p>}
    {job.status === 'running' && <p className="muted">CPU-only inference: text reports usually take 20–90 s, images 1–4 min. You can leave this page; the analysis continues.</p>}
    {job.status === 'failed' && <div className="error" role="alert"><XCircle size={16}/> {job.error}</div>}
    {job.status === 'done' && <>
      {extraction.answer && <div className="model-answer"><span className="eyebrow">MODEL RESPONSE</span><p>{extraction.answer}</p></div>}
      <h3 className="sub">Created for review ({result.created_reports?.length || 0})</h3>
      {result.created_reports?.length ? <div className="analysis-entries">
        {result.created_reports.map((r) => <div className="analysis-entry" key={r.report_id}>
          <strong>{r.activity_id} · {r.activity_name}</strong>
          <span>{r.progress}%{r.quantity ? ` (${r.quantity})` : ''} · {r.reporting_date}</span>
          <small>Report says “{r.activity_text}” <MatchPill status={r.match_status} score={r.score}/>{r.needs_review ? ' · check carefully' : ''}</small>
        </div>)}
      </div> : <p className="muted">No new progress values were found.</p>}
      {result.skipped?.length > 0 && <>
        <h3 className="sub">Not turned into reports ({result.skipped.length})</h3>
        <div className="skipped">{result.skipped.map((s, i) => <div key={i}><strong>{s.activity_text || s.activity_id || 'Item'}</strong>{s.progress != null ? ` · ${s.progress}%` : ''} — {s.reason}</div>)}</div>
      </>}
      {[...(result.document?.warnings || []), ...(extraction.warnings || [])].length > 0 && <div className="warning-list">
        {[...(result.document?.warnings || []), ...(extraction.warnings || [])].map((w, i) => <span key={i}><AlertTriangle size={12}/> {w}</span>)}
      </div>}
      {result.created_reports?.length > 0 && <div className="actions"><button className="primary" onClick={onReview}>Review {result.created_reports.length} report(s) <ChevronRight size={16}/></button></div>}
    </>}
  </section>;
}

export function AnalysePage({ user, ai, onChanged, onReview, activeJob, setActiveJob }) {
  const [file, setFile] = useState(null);
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState('auto');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [jobs, setJobs] = useState([]);
  const allowed = can(user, 'engineer');

  const loadJobs = () => request('/jobs?purpose=report').then(setJobs).catch(() => {});
  useEffect(() => { loadJobs(); }, []);

  async function submit(force = false) {
    if (!file) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const job = await postForm('/reports/upload', { file, instruction: question, mode, force: force ? 'true' : undefined });
      setActiveJob(job.id);
      if (job.duplicate) setMessage(job.message);
      loadJobs();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const textReady = ai?.available && ai?.text_installed;
  const visionReady = ai?.available && ai?.vision_installed;
  return <>
    {!ai?.available && <div className="error" role="alert"><AlertTriangle size={16}/> Ollama is not running. Start the Ollama app; uploads will fail until it is available.</div>}
    {ai?.warnings?.length > 0 && ai.available && <div className="notice">{ai.warnings.join(' · ')}</div>}
    <div className="analyse-grid">
      <section className="panel">
        <span className="eyebrow">SITE REPORTS → STRUCTURED PROGRESS</span>
        <h2>Upload a daily report, site diary, scan or photo</h2>
        <p className="muted">Text, Word and PDF reports are read directly; scans are read with OCR (PaddleOCR models). The local model extracts activities, cumulative progress, dates and issues, then each item is matched to the schedule and placed in the review queue. Nothing changes progress until a manager approves it.</p>
        {!allowed ? <p className="muted">Your role ({user.role}) can view analyses but not upload.</p> : <div className="upload-form">
          <label className="dropzone">
            <FileSearch size={22}/>
            <span>{file ? <><strong>{file.name}</strong> · {(file.size / 1024).toFixed(0)} KB</> : 'Choose a .txt, .docx, .pdf or image file'}</span>
            <input type="file" accept={REPORT_TYPES} onChange={(e) => { setFile(e.target.files[0] || null); setMessage(''); }}/>
          </label>
          <label className="field">Question for the model (optional)
            <textarea rows="2" value={question} maxLength={2000} onChange={(e) => setQuestion(e.target.value)}
              placeholder="Example: Which activities are delayed and why? What is the likely downstream impact?"/>
          </label>
          <fieldset className="modes"><legend>How should the document be read?</legend>
            {MODES.map(([value, label, hint]) => <label key={value} className={mode === value ? 'active' : ''}>
              <input type="radio" name="mode" value={value} checked={mode === value} onChange={() => setMode(value)}/>
              <span><strong>{label}</strong><small>{hint}</small></span>
            </label>)}
          </fieldset>
          <div className="actions">
            <button className="primary" disabled={!file || busy} onClick={() => submit(false)}><Upload size={16}/> {busy ? 'Uploading…' : 'Analyse report'}</button>
            {message && <button onClick={() => submit(true)} disabled={busy}><RefreshCw size={14}/> Analyse again anyway</button>}
          </div>
          {message && <p className="notice">{message}</p>}
          {error && <p className="error" role="alert">{error}</p>}
        </div>}
      </section>
      <section className="panel engine">
        <span className="eyebrow">LOCAL AI ENGINE</span>
        <h2>{ai?.available ? 'Ollama is running' : 'Ollama offline'}</h2>
        <ul className="engine-list">
          <li className={textReady ? 'ok' : 'bad'}><span>Text model</span><strong>{ai?.text_model || '—'}</strong><small>{textReady ? 'installed' : 'missing'}</small></li>
          <li className={visionReady ? 'ok' : 'bad'}><span>Vision model</span><strong>{ai?.vision_model || '—'}</strong><small>{visionReady ? 'installed' : 'missing'}</small></li>
          <li className="ok"><span>OCR</span><strong>RapidOCR (PaddleOCR)</strong><small>{ai?.ocr_enabled === false ? 'disabled' : 'enabled'}</small></li>
          <li className="ok"><span>Matching</span><strong>Sentence Transformers</strong><small>{(ai?.matcher || 'auto').split(' ')[0]}</small></li>
        </ul>
        {ai?.loaded?.length > 0 && <p className="footnote">In memory now: {ai.loaded.join(', ')}</p>}
      </section>
    </div>
    {activeJob && <JobTracker jobId={activeJob} onFinished={() => { onChanged(); loadJobs(); }} onReview={onReview}/>}
    <section className="panel">
      <div className="section-title"><h2>Recent analyses</h2><button onClick={loadJobs}><RefreshCw size={14}/> Refresh</button></div>
      {jobs.length ? <div className="table-scroll"><table><thead><tr><th>#</th><th>File</th><th>Status</th><th>Method</th><th>Time</th><th>By</th><th/></tr></thead>
        <tbody>{jobs.map((j) => <tr key={j.id}>
          <td>{j.id}</td><td>{j.filename}</td>
          <td><span className={`pill ${j.status === 'failed' ? 'warning' : ''}`}>{j.status}</span></td>
          <td>{j.method || '—'}</td><td>{j.duration_s != null ? `${j.duration_s} s` : j.stage || '—'}</td><td>{j.created_by || '—'}</td>
          <td><button onClick={() => setActiveJob(j.id)}>Open</button></td>
        </tr>)}</tbody></table></div> : <p className="muted">No reports analysed yet. Try data/samples/qwen_test_report.txt.</p>}
    </section>
  </>;
}

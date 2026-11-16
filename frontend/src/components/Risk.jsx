import React, { useEffect, useState } from 'react';
import { Download, RefreshCw, TrendingUp } from 'lucide-react';
import { can, download, request } from '../api';

export function RiskPage({ user }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = () => request('/predictions').then((d) => { setData(d); setError(''); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function retrain() {
    setBusy(true);
    try { await request('/predictions/train', { method: 'POST' }); await load(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  if (error && !data) return <div className="error">{error}</div>;
  if (!data) return <div className="panel">Scoring delay risk…</div>;
  const m = data.model.metrics;
  const high = data.predictions.filter((p) => p.risk_level === 'high').length;
  return <>
    {error && <div className="error" role="alert">{error}</div>}
    <section className="stats">
      {[['High-risk activities', high, 'Risk score ≥ 50'],
        ['Expected slip (max)', `${Math.max(0, ...data.predictions.map((p) => p.expected_delay_days))} d`, 'Largest predicted finish delay'],
        ['Model ROC-AUC', m.roc_auc, `Hold-out, ${m.test_rows} synthetic rows`],
        ['Delay-days MAE', `${m.delay_days_mae} d`, 'Regression error on hold-out']].map(([label, value, caption], i) =>
        <article key={label}><div>{label}<TrendingUp size={16}/></div><strong className={i === 0 && high ? 'amber' : ''}>{value}</strong><small>{caption}</small></article>)}
    </section>
    <section className="panel">
      <div className="section-title">
        <div><span className="eyebrow">DELAY PREDICTION · {data.model.library.toUpperCase()}</span><h2>Activities ranked by risk</h2></div>
        <div className="actions">
          <button onClick={() => download('/export/report.xlsx', 'infrasight-project-report.xlsx').catch((e) => setError(e.message))}><Download size={14}/> Project report (Excel)</button>
          <button onClick={() => download('/export/predictions.csv', 'predictions.csv').catch((e) => setError(e.message))}><Download size={14}/> CSV</button>
          {can(user, 'admin') && <button disabled={busy} onClick={retrain}><RefreshCw size={14}/> {busy ? 'Training…' : 'Retrain'}</button>}
        </div>
      </div>
      <div className="table-scroll"><table><thead><tr><th>Activity</th><th>P(delay)</th><th>Expected delay</th><th>Risk</th><th>Downstream</th><th>Main factors</th></tr></thead>
        <tbody>{data.predictions.map((p) => <tr key={p.activity_id}>
          <td><small className="activity-id">{p.activity_id}</small>{p.name}{p.critical && <small className="crit"> · critical</small>}</td>
          <td><div className="prob"><div style={{ width: `${p.delay_probability * 100}%` }} className={p.delay_probability >= 0.5 ? 'hot' : ''}/></div>{p.complete ? 'complete' : `${Math.round(p.delay_probability * 100)}%`}</td>
          <td>{p.complete ? '—' : `${p.expected_delay_days} d`}</td>
          <td><span className={`pill risk-${p.risk_level} ${p.risk_level !== 'low' ? 'warning' : ''}`}>{p.risk_level} · {p.risk_score}</span></td>
          <td>{p.downstream_count}</td>
          <td className="factors">{p.factors.length ? p.factors.map((f) => <span key={f.feature}>{f.label} ({f.value})</span>) : '—'}</td>
        </tr>)}</tbody></table></div>
      <p className="footnote">{data.note} {data.risk_formula}. Model {data.model.version}; accuracy {m.accuracy}, F1 {m.f1}. Features: {data.model.features.join(', ')}.</p>
    </section>
  </>;
}

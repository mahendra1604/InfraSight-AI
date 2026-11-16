import React, { useCallback, useEffect, useState } from 'react';
import { Activity, ArrowUpRight, Bot, Building2, CalendarRange, ChevronRight, FileText, GitBranch, LayoutDashboard, LogOut, Ruler, ShieldCheck, TrendingUp } from 'lucide-react';
import { can, fmtDate, getToken, onUnauthorized, request, setToken } from './api';
import { AnalysePage } from './components/Analyse';
import { DependenciesPage } from './components/Dependencies';
import { Login } from './components/Login';
import { ReviewPage } from './components/Review';
import { RiskPage } from './components/Risk';
import { SchedulePage } from './components/Schedule';
import { SitePlansPage } from './components/SitePlans';

const NAV = [
  ['Overview', LayoutDashboard], ['Analyse reports', Bot], ['Report review', FileText], ['Site plans', Ruler], ['Activities', Activity],
  ['Dependencies', GitBranch], ['Delay risk', TrendingUp], ['Schedule', CalendarRange], ['Audit trail', ShieldCheck],
];
const TAGLINES = {
  Overview: 'A clear view of progress. A better view of what comes next.',
  'Analyse reports': 'Turn daily reports, scans and photos into reviewable progress updates.',
  'Site plans': 'Read printed dimensions from a drawing, set activity quantities and start a draft schedule.',
  'Report review': 'Verify AI suggestions before they change the project.',
  Activities: 'Planned versus actual progress for every schedule activity.',
  Dependencies: 'Critical path, float and the downstream impact of delays.',
  'Delay risk': 'Machine-learning delay probability and risk ranking.',
  Schedule: 'Import and version the project baseline.',
  'Audit trail': 'Every decision, who made it, and the evidence behind it.',
};

function Overview({ data, ai, pending, setTab }) {
  const [risks, setRisks] = useState([]);
  useEffect(() => { request('/predictions').then((d) => setRisks(d.predictions.filter((p) => p.risk_level !== 'low').slice(0, 3))).catch(() => {}); }, [data]);
  return <>
    <section className="stats">{[
      ['Actual progress', `${data.summary.actual}%`, 'Verified activity average'],
      ['Planned progress', `${data.summary.planned}%`, 'Baseline average'],
      ['Behind schedule', data.summary.behind, 'Activities below planned progress'],
      ['Awaiting review', pending.length, 'Reports requiring a decision'],
    ].map(([label, value, caption], i) => <article key={label}><div>{label}<ArrowUpRight size={17}/></div><strong className={i === 2 ? 'amber' : ''}>{value}</strong><small>{caption}</small></article>)}</section>
    <div className="overview-grid">
      <section className="panel">
        <div className="section-title"><h2>Progress by activity</h2><span className="legend"><i/>Actual <i className="planned"/>Planned</span></div>
        {data.activities.map((a) => <div className="chart-row" key={a.id}><div>{a.name}<span>{a.actual}% <small>/ {a.planned}%</small></span></div>
          <div className="track"><div className="baseline" style={{ width: `${a.planned}%` }}/><div className="actual" style={{ width: `${a.actual}%` }}/></div></div>)}
        <p className="footnote">{data.summary.method}</p>
      </section>
      <section className="panel attention">
        <span className="eyebrow">NEXT ACTION</span>
        <h2>Keep your progress<br/>grounded in evidence.</h2>
        <p>{pending.length ? `${pending.length} report(s) are ready for review. Confirm the activity and progress before updating your project.` : 'All reports have been reviewed. Upload a site report to continue.'}</p>
        <button className="primary" onClick={() => setTab(pending.length ? 'Report review' : 'Analyse reports')}>{pending.length ? 'Review reports' : 'Analyse a report'} <ChevronRight size={16}/></button>
        {risks.length > 0 && <div className="attention-note"><TrendingUp size={21}/><p>Highest delay risk: {risks.map((r) => `${r.activity_id} (${Math.round(r.delay_probability * 100)}%)`).join(', ')}<small><button className="linklike" onClick={() => setTab('Delay risk')}>Open delay risk →</button></small></p></div>}
        <div className="attention-note"><Bot size={21}/><p>{ai?.available ? `Local AI ready: ${ai.text_installed ? 'text ✓' : 'text ✗'} · ${ai.vision_installed ? 'vision ✓' : 'vision ✗'}` : 'Local AI offline: start Ollama'}<small>Every review is recorded in the audit trail.</small></p></div>
      </section>
    </div>
  </>;
}

function ActivitiesTable({ data }) {
  return <section className="panel"><div className="section-title"><h2>Activity register</h2><span className="muted">{data.activities.length} activities</span></div>
    <div className="table-scroll"><table><thead><tr><th>Activity</th><th>Predecessors</th><th>Planned window</th><th>Quantity done</th><th>Planned</th><th>Actual</th><th>Variance</th><th>Status</th></tr></thead>
      <tbody>{data.activities.map((a) => <tr key={a.id}><td><small className="activity-id">{a.id}</small>{a.name}</td>
        <td>{a.predecessors.join(', ') || '—'}</td><td>{a.planned_start ? `${fmtDate(a.planned_start)} → ${fmtDate(a.planned_finish)}` : '—'}</td>
        <td>{a.total_quantity ? `${Number(a.actual_quantity).toLocaleString()} / ${Number(a.total_quantity).toLocaleString()} ${a.unit}` : '—'}</td>
        <td>{a.planned}%</td><td>{a.actual}%</td><td>{a.variance > 0 ? '+' : ''}{a.variance} pp</td>
        <td><span className={`pill ${a.variance < 0 ? 'warning' : ''}`}>{a.variance < 0 ? 'Behind' : a.actual === 100 ? 'Complete' : a.variance > 0 ? 'Ahead' : 'On plan'}</span></td></tr>)}</tbody></table></div>
  </section>;
}

function Audit({ data }) {
  return <section className="panel"><h2>Review history</h2>
    {data.audit.length ? data.audit.map((a) => <div className="audit-row" key={a.id}><ShieldCheck size={18}/><div>
      <strong>{a.action === 'set_quantity' ? 'Activity quantity set' : `Report ${a.report_id} · ${a.action}`}{a.actor ? ` · ${a.actor}` : ''}{a.created_at ? ` · ${fmtDate(a.created_at)}` : ''}</strong><p>{a.details}</p></div></div>)
      : <p className="muted">Review a report to create the first audit entry.</p>}
  </section>;
}

const slug = (name) => name.toLowerCase().replace(/\s+/g, '-');
const tabFromHash = () => NAV.find(([name]) => slug(name) === window.location.hash.replace(/^#\/?/, ''))?.[0] || 'Overview';

export function App() {
  const [config, setConfig] = useState(null);
  const [user, setUser] = useState(null);
  const [tab, setTabState] = useState(tabFromHash);
  const setTab = useCallback((name) => { setTabState(name); window.location.hash = `/${slug(name)}`; }, []);
  useEffect(() => {
    const onHash = () => setTabState(tabFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  const [data, setData] = useState(null);
  const [ai, setAi] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [activeJob, setActiveJob] = useState(null);

  const signOut = useCallback(() => { setToken(null); setUser(null); setData(null); }, []);
  useEffect(() => onUnauthorized(signOut), [signOut]);

  useEffect(() => {
    request('/auth/config').then(async (cfg) => {
      setConfig(cfg);
      if (!cfg.auth_enabled) { setUser({ username: 'local', display_name: 'Local demo user', role: 'admin' }); return; }
      if (getToken()) setUser(await request('/auth/me').catch(() => null));
    }).catch((e) => setError(e.message));
  }, []);

  const refresh = useCallback(async () => {
    const [project, summary, activities, reports, audit] = await Promise.all(['/project', '/summary', '/activities', '/reports', '/audit'].map((p) => request(p)));
    setData({ project, summary, activities, reports, audit });
  }, []);
  const refreshAi = useCallback(() => request('/ai/status').then(setAi).catch(() => setAi({ available: false })), []);

  useEffect(() => {
    if (!user) return;
    refresh().catch((e) => setError(e.message));
    refreshAi();
    const id = setInterval(refreshAi, 30000);
    return () => clearInterval(id);
  }, [user, refresh, refreshAi]);

  async function run(fn, success) {
    setBusy(true); setError(''); setNotice('');
    try { await fn(); await refresh(); if (success) setNotice(success); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  if (!config && !error) return <div className="login-page"><div className="panel">Connecting to InfraSight…</div></div>;
  if (!user) return config ? <Login config={config} onLogin={setUser}/> : <div className="login-page"><div className="error">{error}</div></div>;

  const pending = data?.reports.filter((r) => r.status === 'pending') || [];
  const aiLabel = !ai ? 'checking AI…' : ai.available ? `AI ready · ${ai.text_model}${ai.vision_installed ? ` + ${ai.vision_model}` : ''}` : 'AI offline (start Ollama)';
  return <div className="shell">
    <aside>
      <a className="brand" href="/" aria-label="InfraSight home"><span className="brand-icon"><Building2 size={23}/></span>InfraSight<span className="ai">AI</span></a>
      <div className="workspace-label">WORKSPACE</div>
      <div className="workspace"><span className="initials">RI</span><div>Riverside project<small>Synthetic demo project</small></div></div>
      <nav>{NAV.map(([name, Icon]) => <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}><Icon size={18}/>{name}{name === 'Report review' && pending.length > 0 && <b>{pending.length}</b>}</button>)}</nav>
      <div className="sidebar-bottom"><span className="dot"/> {ai?.available ? 'Local AI online' : 'Local AI offline'} <small>v0.3 · six-module prototype</small></div>
    </aside>
    <main>
      <header><div>Workspace <ChevronRight size={14}/> Riverside Infrastructure <ChevronRight size={14}/> {tab}</div>
        <div className="user-chip"><span className="avatar">{user.display_name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}</span>
          <span>{user.display_name}<small>{user.role}</small></span>
          {config?.auth_enabled && <button onClick={signOut} title="Sign out" aria-label="Sign out"><LogOut size={15}/></button>}
        </div>
      </header>
      <div className="content">
        <div className="page-heading"><div><div className="eyebrow">PROJECT INTELLIGENCE</div><h1>{tab === 'Overview' ? 'Project overview' : tab}</h1><p>{TAGLINES[tab]}</p></div>
          {can(user, 'engineer') && tab !== 'Analyse reports' && <button className="upload" onClick={() => setTab('Analyse reports')}><Bot size={16}/> Analyse a report</button>}
        </div>
        <div className="demo"><span className="dot"/><strong>Demo workspace</strong><span>Synthetic data · as of {fmtDate(data?.project.as_of)} · {aiLabel} · delay model trained on synthetic history</span></div>
        {error && <div role="alert" className="error">{error} {!data && <button onClick={() => run(async () => {})}>Retry connection</button>}</div>}
        {notice && <div role="status" className="notice">{notice}</div>}
        {!data ? <div className="panel">{error ? 'Start the backend on port 8000, then retry.' : 'Loading your project…'}</div> : <>
          {tab === 'Overview' && <><Overview data={data} ai={ai} pending={pending} setTab={setTab}/><ActivitiesTable data={data}/></>}
          {tab === 'Analyse reports' && <AnalysePage user={user} ai={ai} activeJob={activeJob} setActiveJob={setActiveJob}
            onChanged={() => { refresh(); refreshAi(); }} onReview={() => setTab('Report review')}/>}
          {tab === 'Report review' && <ReviewPage data={data} user={user} busy={busy} run={run}/>}
          {tab === 'Site plans' && <SitePlansPage user={user} data={data} onChanged={refresh}/>}
          {tab === 'Activities' && <ActivitiesTable data={data}/>}
          {tab === 'Dependencies' && <DependenciesPage key={data.activities.map((a) => `${a.id}${a.actual}`).join()}/>}
          {tab === 'Delay risk' && <RiskPage user={user} key={data.activities.map((a) => a.actual).join()}/>}
          {tab === 'Schedule' && <SchedulePage user={user} onChanged={refresh}/>}
          {tab === 'Audit trail' && <Audit data={data}/>}
        </>}
        <footer>InfraSight AI <span>Local LLM extraction · semantic matching · CPM · delay prediction — built for a six-member capstone team</span></footer>
      </div>
    </main>
  </div>;
}

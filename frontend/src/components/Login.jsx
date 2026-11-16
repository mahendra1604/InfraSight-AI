import React, { useState } from 'react';
import { Building2, LogIn } from 'lucide-react';
import { post, request, setToken } from '../api';

export function Login({ config, onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError('');
    try {
      const result = await request('/auth/login', post({ username, password }));
      setToken(result.token);
      onLogin(result.user);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return <div className="login-page">
    <form className="panel login" onSubmit={submit}>
      <div className="brand login-brand"><span className="brand-icon"><Building2 size={23}/></span>InfraSight<span className="ai">AI</span></div>
      <h1>Sign in</h1>
      <p className="muted">Infrastructure progress monitoring, dependency analysis and delay prediction.</p>
      <label>Username<input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required/></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required/></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary" disabled={busy}><LogIn size={16}/> {busy ? 'Signing in…' : 'Sign in'}</button>
      {config?.demo_accounts?.length > 0 && <div className="demo-accounts">
        <span className="eyebrow">DEMO ACCOUNTS (local only)</span>
        {config.demo_accounts.map((a) => <button type="button" key={a.username} onClick={() => { setUsername(a.username); setPassword(a.password); }}>
          <strong>{a.username}</strong><small>{a.role}</small>
        </button>)}
      </div>}
    </form>
  </div>;
}

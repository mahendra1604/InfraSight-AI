import React, { lazy, Suspense, useState } from 'react';
import { motion } from 'motion/react';
import { Building2, LogIn } from 'lucide-react';
import { post, request, setToken } from '../api';

const LoginBackdrop = lazy(() => import('./three/LoginBackdrop'));

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

  return <div className="login-page with-backdrop">
    <Suspense fallback={null}><LoginBackdrop/></Suspense>
    <motion.form className="panel login" onSubmit={submit} initial={{ opacity: 0, y: 24, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}>
      <div className="brand login-brand"><motion.span className="brand-icon" initial={{ rotate: -20, scale: 0.6 }} animate={{ rotate: 0, scale: 1 }}
        transition={{ type: 'spring', stiffness: 260, damping: 14, delay: 0.2 }}><Building2 size={23}/></motion.span>InfraSight<span className="ai">AI</span></div>
      <h1>Sign in</h1>
      <p className="muted">Infrastructure progress monitoring, dependency analysis and delay prediction.</p>
      <label>Username<input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required/></label>
      <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required/></label>
      {error && <motion.p className="error" role="alert" initial={{ x: -8 }} animate={{ x: [8, -6, 4, 0] }} transition={{ duration: 0.35 }}>{error}</motion.p>}
      <motion.button className="primary" disabled={busy} whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}><LogIn size={16}/> {busy ? 'Signing in…' : 'Sign in'}</motion.button>
      {config?.demo_accounts?.length > 0 && <div className="demo-accounts">
        <span className="eyebrow">DEMO ACCOUNTS (local only)</span>
        {config.demo_accounts.map((a, i) => <motion.button type="button" key={a.username} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 + i * 0.07 }} whileHover={{ y: -2 }} onClick={() => { setUsername(a.username); setPassword(a.password); }}>
          <strong>{a.username}</strong><small>{a.role}</small>
        </motion.button>)}
      </div>}
    </motion.form>
  </div>;
}

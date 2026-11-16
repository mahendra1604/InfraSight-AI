const TOKEN_KEY = 'infrasight.token';

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export function setToken(token) {
  try { token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
}

let unauthorizedHandler = () => {};
export function onUnauthorized(handler) { unauthorizedHandler = handler; }

/** Fetch an API path. Never calls res.json() on a non-JSON body (e.g. a proxy error page). */
export async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  let res;
  try {
    res = await fetch('/api' + path, { ...options, headers });
  } catch {
    throw new Error('Cannot reach the InfraSight API. Start the backend on port 8000, then retry.');
  }
  const type = res.headers.get('content-type') || '';
  let data = null;
  if (type.includes('application/json')) {
    try { data = await res.json(); } catch { data = null; }
  } else {
    data = await res.text().catch(() => '');
  }
  if (res.status === 401 && !path.startsWith('/auth/login')) unauthorizedHandler();
  if (!res.ok) {
    const detail = data && typeof data === 'object' ? data.detail : null;
    let message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d) => `${(d.loc || []).slice(-1)[0] || 'field'}: ${d.msg}`).join('; ') : null;
    if (!message) {
      message = [502, 503, 504].includes(res.status)
        ? 'The API did not answer (backend stopped or proxy timeout). Check the backend terminal.'
        : `Request failed (HTTP ${res.status}).`;
    }
    const error = new Error(message);
    error.status = res.status;
    throw error;
  }
  return data;
}

export const post = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function postForm(path, fields) {
  const body = new FormData();
  Object.entries(fields).forEach(([key, value]) => { if (value !== undefined && value !== null) body.append(key, value); });
  return request(path, { method: 'POST', body });
}

/** Download a file that needs the Authorization header. */
export async function download(path, filename) {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch('/api' + path, { headers });
  if (!res.ok) throw new Error(`Download failed (HTTP ${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const ROLE_RANK = { viewer: 0, engineer: 1, manager: 2, admin: 3 };
export const can = (user, role) => (ROLE_RANK[user?.role] ?? -1) >= ROLE_RANK[role];

export const fmtDate = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
};

export const pct = (value, digits = 0) => (value == null ? '—' : `${Number(value).toFixed(digits)}%`);

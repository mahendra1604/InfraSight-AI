import { defineConfig } from 'vite';

// API_URL lets you point the dev server at a backend on another port, e.g.
//   $env:API_URL = 'http://127.0.0.1:8010'; npm.cmd run dev -- --port 5180
const apiUrl = process.env.API_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  server: { proxy: { '/api': apiUrl } },
  // The 3D libraries (three.js + React Three Fiber) form one large chunk that is lazy-loaded
  // only when a 3D view is shown, so the warning about it is expected.
  build: { chunkSizeWarningLimit: 1100 },
});

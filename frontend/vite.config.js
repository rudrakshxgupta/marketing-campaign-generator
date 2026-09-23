import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API is same-origin in dev, so uploads and SSE work without CORS.
    //
    // 127.0.0.1 and not 'localhost', which is not a style preference.
    // uvicorn binds IPv4 only by default. On Windows, Node 17+ resolves
    // 'localhost' to ::1 first and no longer reorders to prefer IPv4, so the
    // proxy dials [::1]:8000 where nothing is listening. Every /api call then
    // fails while the page itself loads perfectly -- the interface appears,
    // and does nothing at all.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
    },
  },
})

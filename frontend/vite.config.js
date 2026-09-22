import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The API is same-origin in dev, so uploads and SSE work without CORS.
    proxy: { '/api': 'http://localhost:8000', '/healthz': 'http://localhost:8000' },
  },
})

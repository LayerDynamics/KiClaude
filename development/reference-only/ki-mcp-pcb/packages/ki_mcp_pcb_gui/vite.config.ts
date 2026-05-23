import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/  +  https://vitest.dev/config/
export default defineConfig({
  plugins: [react()],
  // In `npm run dev`, proxy API calls to the FastAPI backend so the
  // frontend uses same-origin `/api/...` paths in dev and in production
  // (where the backend serves the built bundle itself). `ws: true` also
  // upgrades the co-pilot WebSocket (`/api/agent`).
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})

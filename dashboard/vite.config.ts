import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served by the contextlab proxy at /_contextlab/app/ in production;
// `npm run dev` proxies data endpoints to a locally running proxy.
export default defineConfig({
  plugins: [react()],
  base: '/_contextlab/app/',
  server: {
    proxy: {
      '/_contextlab/ws': { target: 'ws://127.0.0.1:8484', ws: true },
      '/_contextlab/recent': 'http://127.0.0.1:8484',
    },
  },
})

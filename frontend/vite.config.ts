import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { localProxyGuard } from './devProxyBoundary.ts'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), { name: 'local-api-proxy-boundary', configureServer(server) {
    server.middlewares.use(localProxyGuard)
  }, configurePreviewServer(server) {
    server.middlewares.use(localProxyGuard)
  } }],
  preview: { host: '127.0.0.1' },
  server: {
    host: '127.0.0.1',
    port: 3000,
    proxy: {
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/admin': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})

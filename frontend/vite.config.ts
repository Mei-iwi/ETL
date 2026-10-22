import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
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
    strictPort: true,
    proxy: {
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        configure: configureAuthProxy,
      },
      '/admin': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        configure: configureAuthProxy,
      },
      '/docs': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        configure: configureAuthProxy,
      },
      '/openapi.json': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
        configure: configureAuthProxy,
      },
    },
  },
})

// Server-only configuration: never expose the proxy key through VITE_* or define.
function configureAuthProxy(proxy: import('vite').HttpProxy.ProxyServer) {
  const root = fileURLToPath(new URL('../', import.meta.url))
  const env = { ...loadEnv('development', root, 'AUTH_'), ...process.env }
  if (!env.AUTH_MODE || env.AUTH_MODE === 'legacy_local') return
  const keyPath = env.AUTH_PROXY_KEY_FILE
  if (!keyPath) throw new Error('Local auth proxy requires AUTH_PROXY_KEY_FILE in the Backend .env')
  const key = readFileSync(keyPath, 'utf8').trim()
  if (!/^[a-fA-F0-9]{64}$/.test(key)) throw new Error('Local auth proxy key has an invalid format')
  proxy.on('proxyReq', (outgoing) => {
    outgoing.setHeader('x-etl-proxy-key', key)
  })
}

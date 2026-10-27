// Built React + real headless browser, isolated HTTP fixtures; no DB/model.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, resolve, sep } from 'node:path'
import test from 'node:test'
import { searchResponse } from './helpers/searchResponses.mjs'

const dist = resolve(process.env.ETL_FOCUS_DIST || '../.test-tmp/frontend-search-focus')
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const dev = process.env.ETL_FOCUS_DEV === '1'

test('focus preserves an in-flight search for the same verified user; expiry still revokes', {
    timeout: 90000, skip: !existsSync(executable) || (!dev && !existsSync(join(dist, 'index.html'))),
}, async () => {
    const vite = dev ? await (await import('vite')).createServer({ configFile: false, root: resolve('.'),
        cacheDir: await mkdtemp(resolve('../.test-tmp/vite-focus-deps-')),
        cacheDir: await mkdtemp(resolve('../.test-tmp/vite-focus-deps-')),
        plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa',
        server: { middlewareMode: true, hmr: false } }) : null
    let postCount = 0, authChecks = 0, expired = false, release
    const server = createServer(async (req, res) => {
        const path = new URL(req.url, 'http://localhost').pathname
        const json = (body, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)) }
        if (path === '/api/v1/auth/config') return json({ mode: 'master_users', login_required: true })
        if (path === '/api/v1/auth/me') {
            authChecks++
            return expired ? json({ detail: { code: 'AUTH_SESSION_EXPIRED' } }, 401)
                : json({ id: 'master:focus-reader', mode: 'master_users', role: 'VIEWER', roles: ['viewer'],
                    permissions: [], resource_ids: [], admin_feature_enabled: false })
        }
        if (path === '/api/v1/auth/csrf') return json({ csrf_token: 'isolated-focus-fixture' })
        if (path === '/api/v1/search/model-status') return json({ state: 'READY', ready: true })
        if (path === '/api/v1/search') {
            postCount++
            let body = ''
            for await (const part of req) body += part
            release = () => json(searchResponse(JSON.parse(body)))
            return
        }
        if (path.startsWith('/api/')) return json({ items: [], pagination: { page: 1, size: 100, total: 0, total_pages: 0 } })
        if (vite) return vite.middlewares(req, res)
        const file = path.startsWith('/assets/') ? resolve(dist, '.' + path) : join(dist, 'index.html')
        if (!file.startsWith(dist + sep)) { res.writeHead(403); return res.end() }
        try {
            const body = await readFile(file)
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[extname(file)] || 'application/octet-stream' })
            res.end(body)
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const directory = await mkdtemp(resolve('../.test-tmp/search-focus-browser-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', `--user-data-dir=${directory}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map()
    async function wait(fn) {
        for (let i = 0; i < 150; i++) { const value = await fn(); if (value) return value; await delay(50) }
        throw new Error('Search focus assertion timed out')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)) }, 20000)
            pending.set(id, { resolve: value => { clearTimeout(timeout); resolve(value) }, reject })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
        assert.equal(result.exceptionDetails, undefined)
        return result.result.value
    }
    try {
        const port = await wait(async () => { try { return Number((await readFile(join(directory, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null } })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const message = JSON.parse(event.data), item = pending.get(message.id)
            if (item) { pending.delete(message.id); if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result) }
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable'); await call('Runtime.enable')
        await call('Page.navigate', { url: origin + '/search/query-methods?q=synthetic&method=bow' })
        await wait(() => release && postCount === 1)
        const before = authChecks
        await evaluate("for(let i=0;i<5;i++) window.dispatchEvent(new Event('focus'))")
        await wait(() => authChecks > before)
        await delay(150)
        assert.equal(authChecks, before + 1, 'focus burst shares one session verification')
        release()
        await wait(() => evaluate("!!document.querySelector('article') || !!document.querySelector('[role=alert]')"))
        assert.equal(await evaluate("!!document.querySelector('article')"), true,
            await evaluate('document.body.textContent'))
        assert.equal(postCount, 1, 'same-user focus must not issue a second heavy search')
        await evaluate("window.dispatchEvent(new Event('focus'))")
        await delay(150)
        assert.equal(postCount, 1)
        expired = true
        await evaluate("window.dispatchEvent(new Event('focus'))")
        await wait(() => evaluate("location.pathname === '/login'"))
        assert.equal(await evaluate("!!document.querySelector('article')"), false)
    } finally {
        ws?.close(); browser.kill()
        server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
        await vite?.close()
    }
})

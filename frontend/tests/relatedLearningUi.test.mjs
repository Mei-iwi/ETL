// Real built UI with isolated graph-produced fixtures, never live credentials.
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { basename, dirname, extname, join, resolve, sep } from 'node:path'

const dist = resolve(process.env.ETL_TEST_DIST || 'frontend/dist-safe')
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const relatedNode = `document.querySelector(${JSON.stringify('section[aria-label="Học liệu liên quan"]')})`

for (const role of ['ADMIN', 'VIEWER']) {
test(`related learning fixture renders, navigates and hides on fallback (${role})`, { timeout: 60000 }, async () => {
    const graphResult = process.env.ETL_RELATED_UI_FIXTURE
        ? JSON.parse(await readFile(process.env.ETL_RELATED_UI_FIXTURE, 'utf8'))
        : { items: [{ resource_id: 'other', title: 'Other', reason_label: 'Cùng môn học và khối lớp', url: '/library/other' }] }
    assert.equal(graphResult.items[0].resource_id, 'other')
    let mode = 'active', relatedRequests = 0
    const server = createServer(async (req, res) => {
        const path = new URL(req.url, 'http://127.0.0.1').pathname
        const json = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)) }
        if (path === '/api/v1/auth/config') return json({ mode: 'master_users', login_required: true, admin_feature_enabled: true })
        if (path === '/api/v1/auth/me') return json({ id: 'master:fixture', role, roles: [role.toLowerCase()], mode: 'master_users', resource_ids: [], permissions: [], admin_feature_enabled: true })
        if (path === '/health') return json({ status: 'ok', database: 'ok' })
        if (/^\/api\/v1\/resources\/(seed|other)$/.test(path)) {
            const id = path.split('/').at(-1)
            return json({ id, title: id === 'seed' ? 'Graph relation fixture' : 'Other', source_origin: 'POSTGRESQL',
                publication_status: 'PUBLISHED', is_active: true, subjects: [], grade_levels: [], created_at: '2026-01-01', updated_at: '2026-01-01' })
        }
        if (path.endsWith('/related-learning')) {
            relatedRequests++
            return json(mode === 'active' && path.includes('/seed/') ? graphResult : { items: [] })
        }
        if (path.endsWith('/versions')) return json([])
        if (path.startsWith('/api/')) return json({}, 404)
        const target = path.startsWith('/assets/') ? resolve(dist, '.' + path) : join(dist, 'index.html')
        assert.ok(target.startsWith(dist + sep))
        try {
            res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[extname(target)] || 'application/octet-stream' })
            res.end(await readFile(target))
        } catch { res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const profile = await mkdtemp(join(tmpdir(), 'etl-related-ui-'))
    const browser = spawn(process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
        '--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
    ], { windowsHide: true, stdio: 'ignore' })
    let socket, sequence = 0
    const pending = new Map()
    const errors = []
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout ${method}`)) }, 10000)
            pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject })
            socket.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(result.exceptionDetails, undefined)
        return result.result.value
    }
    async function waitFor(fn) {
        for (let i = 0; i < 100; i++) { const value = await fn(); if (value) return value; await delay(100) }
        throw new Error('Related UI assertion timed out')
    }
    try {
        const port = await waitFor(async () => {
            try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
        })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        socket.addEventListener('message', event => {
            const data = JSON.parse(event.data), request = pending.get(data.id)
            if (data.method === 'Runtime.exceptionThrown') errors.push('BROWSER_EXCEPTION')
            if (request) { pending.delete(data.id); data.error ? request.reject(new Error('CDP error')) : request.resolve(data.result) }
        })
        await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }))
        await call('Page.enable'); await call('Runtime.enable')
        await call('Page.navigate', { url: origin + '/library/seed' })
        await waitFor(() => evaluate(`Boolean(${relatedNode}?.querySelector('a'))`))
        assert.equal(await evaluate(`${relatedNode}.querySelector('a').textContent`), 'Other')
        assert.equal(await evaluate(`${relatedNode}.textContent.includes('Cùng môn học và khối lớp')`), true)
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate(`${relatedNode}.getBoundingClientRect().right <= innerWidth`), true)
        const evidence = '.local/neo4j/active-20261105'
        await mkdir(evidence, { recursive: true })
        await evaluate(`${relatedNode}.scrollIntoView({block:'center'})`)
        const screenshot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
        await writeFile(join(evidence, `ui-related-${role.toLowerCase()}.png`), Buffer.from(screenshot.data, 'base64'))
        await evaluate(`${relatedNode}.querySelector('a').click()`)
        await waitFor(() => evaluate('location.pathname === "/library/other" && document.querySelector("h1")?.textContent.includes("Other")'))
        for (mode of ['shadow', 'disabled', 'down']) {
            await call('Page.navigate', { url: origin + '/library/seed' })
            await waitFor(() => evaluate('document.querySelector("h1")?.textContent.includes("Graph relation fixture")'))
            await delay(300)
            assert.equal(await evaluate(`Boolean(${relatedNode})`), false)
        }
        assert.deepEqual(errors, [])
        assert.ok(relatedRequests <= 10, 'bounded detail requests, not one request per related item')
    } finally {
        if (socket?.readyState === WebSocket.OPEN) { socket.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' })); socket.close() }
        browser.kill(); server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
        assert.equal(dirname(profile), tmpdir()); assert.ok(basename(profile).startsWith('etl-related-ui-'))
        await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
    }
})
}

// Built React in Edge; isolated HTTP fixtures, never modifies Dev databases.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, extname, join, resolve, sep } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = process.env.ETL_TEST_DIST ? dirname(resolve(process.env.ETL_TEST_DIST)) : resolve(dirname(fileURLToPath(import.meta.url)), '..')
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('SOURCE/legacy are read-only, LOCAL saves and server ownership races remain visible', {
    skip: !existsSync(executable) || !existsSync(join(root, 'dist/index.html')), timeout: 90000,
}, async () => {
    let patches = 0, denyPatch = false
    const message = 'Học liệu được quản lý từ SOURCE. Vui lòng sửa tại nguồn và đồng bộ lại.'
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost')
        const json = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)) }
        if (url.pathname === '/health') return json({ status: 'ok', database: 'ok' })
        if (url.pathname === '/api/v1/auth/config') return json({ mode: 'legacy_local' })
        const catalogs = { '/api/v1/resource-types': [{ code: 'DOC', name: 'Tài liệu' }],
            '/api/v1/providers': [{ id: 'p1', name: 'Provider', provider_type: 'INTERNAL' }],
            '/api/v1/grade-levels': [], '/api/v1/subjects': [] }
        if (url.pathname in catalogs) return json({ items: catalogs[url.pathname], pagination: { total_pages: 1 } })
        if (url.pathname.endsWith('/metadata') && req.method === 'PATCH') {
            patches++
            let text = ''; for await (const part of req) text += part
            if (denyPatch) return json({ detail: { code: 'RESOURCE_SOURCE_OWNED_READ_ONLY', message } }, 409)
            return json({ id: 'local', ...JSON.parse(text), updated_at: '2026-01-01T00:00:00Z' })
        }
        if (/^\/api\/v1\/resources\/(source|local|legacy)$/.test(url.pathname)) {
            const id = url.pathname.split('/').pop()
            return json({ id, title: 'Học liệu kiểm thử', resource_type_code: 'DOC', provider_id: 'p1',
                publication_status: 'PUBLISHED', subjects: [], grade_levels: [],
                ...(id === 'legacy' ? {} : { source_origin: id === 'local' ? 'LOCAL' : 'POSTGRESQL' }) })
        }
        if (url.pathname.startsWith('/api/')) return json({ detail: 'Not found' }, 404)
        const target = url.pathname.startsWith('/assets/') ? resolve(root, 'dist', '.' + url.pathname) : join(root, 'dist/index.html')
        if (!target.startsWith(join(root, 'dist') + sep)) { res.writeHead(403); return res.end() }
        try {
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[extname(target)] || 'application/octet-stream' })
            res.end(await readFile(target))
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const profile = await mkdtemp(join(tmpdir(), 'etl-ownership-ui-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map()
    async function waitFor(fn) {
        for (let i = 0; i < 300; i++) { const value = await fn(); if (value) return value; await delay(100) }
        throw new Error('Ownership UI expectation timed out')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout ${method}`)) }, 10000)
            pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject: error => { clearTimeout(timer); reject(error) } })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(result.exceptionDetails, undefined)
        return result.result.value
    }
    async function capture(name) {
        const folder = resolve(root, '../.test-tmp/resource-ownership-ui')
        await mkdir(folder, { recursive: true })
        const shot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
        await writeFile(join(folder, name + '.png'), Buffer.from(shot.data, 'base64'))
    }
    async function open(id) {
        await call('Page.navigate', { url: `${origin}/library/${id}/update-version` })
        await waitFor(() => evaluate("!!document.querySelector('#title')"))
    }
    try {
        const port = await waitFor(async () => {
            try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
        })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const data = JSON.parse(event.data), request = pending.get(data.id)
            if (request) { pending.delete(data.id); if (data.error) request.reject(new Error(data.error.message)); else request.resolve(data.result) }
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false })
        await open('source')
        assert.equal(await evaluate("document.querySelector('#title').disabled"), true)
        assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), true)
        assert.ok(await evaluate(`document.body.textContent.includes(${JSON.stringify(message)})`))
        await evaluate("document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))")
        assert.equal(patches, 0)
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Hủy').disabled"), false)
        await capture('source-readonly')
        await open('legacy')
        assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), true)
        assert.ok(await evaluate("document.body.textContent.includes('Chưa xác minh')"))
        await open('local')
        assert.equal(await evaluate("document.querySelector('#title').disabled"), false)
        await evaluate("document.querySelector('button[type=submit]').click()")
        await waitFor(() => patches === 1 && evaluate("document.body.textContent.includes('Cập nhật thông tin học liệu thành công.')"))
        denyPatch = true
        await open('local')
        await evaluate("document.querySelector('button[type=submit]').click()")
        await waitFor(() => evaluate(`document.querySelector('[role=alert]')?.textContent.includes(${JSON.stringify(message)})`))
        assert.equal(patches, 2)
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        await capture('ownership-error-mobile')
    } finally {
        if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' })); ws.close() }
        browser.kill(); server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
        if (profile.startsWith(join(tmpdir(), 'etl-ownership-ui-'))) await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }).catch(() => {})
    }
})

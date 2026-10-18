// Built React in a real browser against disposable HTTP fixtures; never Dev.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { extname, join, resolve, sep } from 'node:path'
import test from 'node:test'

const browserPath = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const root = resolve('frontend/dist')
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
test('evaluation history pages, opens saved results, reruns and recovers malformed lists', {
    skip: !existsSync(browserPath) || !existsSync(join(root, 'index.html')), timeout: 60000,
}, async () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({
        id: '01M4ZXS1XJ2B4H42CFY24KJY8' + i, dataset_id: '01M4ZXS1XJ2B4H42CFY24KJY8C',
        dataset_name: 'History ' + (8 - i), created_at: '2026-10-' + (18 - i) + 'T10:00:00Z',
        finished_at: '2026-10-18T10:00:12Z', duration_ms: 12000, status: 'COMPLETED', query_count: 18,
    }))
    let malformed = false, posts = 0
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost')
        const json = value => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)) }
        if (req.method === 'POST') posts++
        if (url.pathname === '/health') return json({ status: 'ok', database: 'ok' })
        if (url.pathname === '/api/v1/evaluations/runs') {
            const page = Number(url.searchParams.get('page'))
            return json(malformed ? null : { items: rows.slice((page - 1) * 7, page * 7), page, size: 7, total: 8, total_pages: 2 })
        }
        if (url.pathname.startsWith('/api/v1/evaluations/runs/')) {
            const row = rows.find(row => url.pathname.endsWith(row.id))
            return json({ ...row, dataset_hash: 'fixture', corpus_fingerprint: 'fixture', relevance_threshold: 1,
                snapshot: { model: { id: 'fixture', version: 'test', dimension: 384 } },
                queries: [], summary: {}, warnings: [], observations: [], pagination: { total: 0 } })
        }
        if (url.pathname.startsWith('/api/')) { res.writeHead(404); return res.end() }
        const file = url.pathname.startsWith('/assets/') ? resolve(root, '.' + url.pathname) : join(root, 'index.html')
        if (!file.startsWith(root + sep)) {
            res.writeHead(403); return res.end()
        }
        try {
            const bytes = await readFile(file)
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[extname(file)] || 'application/octet-stream' })
            res.end(bytes)
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = 'http://127.0.0.1:' + server.address().port
    const profile = await mkdtemp(join(tmpdir(), 'etl-history-ui-'))
    const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map()
    async function waitFor(fn) {
        for (let i = 0; i < 200; i++) { const value = await fn(); if (value) return value; await delay(100) }
        throw new Error('History UI timed out')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout')) }, 5000)
            pending.set(id, value => { clearTimeout(timer); resolve(value) })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const value = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(value.exceptionDetails, undefined)
        return value.result.value
    }
    const click = label => evaluate('(Array.from(document.querySelectorAll("button,a")).find(e => e.textContent === ' + JSON.stringify(label) + ')).click()')
    try {
        const port = await waitFor(async () => {
            try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
        })
        const pages = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => { const value = JSON.parse(event.data); if (pending.has(value.id)) { pending.get(value.id)(value.result); pending.delete(value.id) } })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        await call('Page.navigate', { url: origin + '/experiments/results' })
        await waitFor(() => evaluate('document.querySelectorAll("tbody tr").length === 7'))
        assert.equal(await evaluate('document.querySelector("tbody tr").textContent.includes("History 8")'), true)
        assert.equal(await evaluate('document.querySelectorAll("input").length'), 0)
        assert.equal(await evaluate('document.querySelector("tbody tr").textContent.includes("12 giây")'), true)
        await click('Tiếp')
        await waitFor(() => evaluate('document.querySelectorAll("tbody tr").length === 1 && document.body.textContent.includes("Trang 2/2")'))
        await click('Xem kết quả')
        await waitFor(() => evaluate('!!Array.from(document.querySelectorAll("h2")).find(e => e.textContent === "History 1")'))
        assert.equal(await evaluate('document.body.textContent.includes("Trang 2/2")'), true)
        assert.equal(await evaluate('new URLSearchParams(location.search).get("run_id")'), rows[7].id)
        assert.equal(posts, 0)
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true)
        malformed = true
        await click('Tải lại')
        await waitFor(() => evaluate('!!document.querySelector("[role=alert]")'))
        await waitFor(() => evaluate('!document.querySelector("section[aria-busy=true]")'))
        malformed = false
        await click('Tải lại')
        await waitFor(() => evaluate('!!document.querySelector("tbody tr") && !document.querySelector("[role=alert]")'))
        await click('Chạy lại')
        await waitFor(() => evaluate('location.pathname === "/experiments/run"'))
        assert.equal(await evaluate('new URLSearchParams(location.search).get("dataset_id")'), rows[7].dataset_id)
        assert.equal(posts, 0)
    } finally {
        ws?.close()
        browser.kill()
        await new Promise(resolve => server.close(resolve))
        await delay(400)
        await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
    }
})

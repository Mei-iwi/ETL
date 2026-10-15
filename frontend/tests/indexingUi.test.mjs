// Real built React UI in headless Edge; isolated HTTP fixtures, no live API/DB.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, extname, join, resolve, sep } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const browserPath = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('index UI selects a version, never POSTs on mount, queues and displays runtime errors', {
    skip: !existsSync(browserPath) || !existsSync(join(root, 'dist/index.html')),
    timeout: 60000,
}, async () => {
    let postCount = 0
    let blocked = false
    const server = createServer(async (req, res) => {
        const path = new URL(req.url, 'http://localhost').pathname
        function json(body, status = 200) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)) }
        if (path === '/health') return json({ status: 'ok', database: 'ok' })
        if (path === '/api/v1/resources') return json({ items: [{ id: 'resource-test', title: 'Học liệu kiểm thử' }], pagination: { total: 1 } })
        if (path === '/api/v1/resources/resource-test/versions') return json([{ id: 'version-test', resource_id: 'resource-test', version_number: 1 }])
        if (path.endsWith('/chunks')) {
            const offset = Number(new URL(req.url, 'http://localhost').searchParams.get('offset'))
            return json({ resource_version_id: 'version-test', index_run_id: 'old-run', index_status: 'READY', total: 11, offset, limit: 10,
                items: Array.from({ length: offset === 0 ? 10 : 1 }, (_, i) => ({ id: `chunk-${offset + i + 1}`, content_unit_id: 'unit-test',
                    chunk_sequence: offset + i + 1, unit_chunk_sequence: 1, page_from: 2, page_to: 2,
                    char_start: 0, char_end: 17, text: 'Văn bản tiếng Việt', bow_top_terms: [{ term: 'việt', frequency: 1 }],
                    bow_term_count: 4, vector_ready: true, embedding_model: 'ui-test-only', embedding_dimension: 3 })) })
        }
        if (path.endsWith('/index') && req.method === 'POST') {
            postCount++
            return blocked ? json({ detail: { code: 'BLOCKED_EMBEDDING_RUNTIME', message: 'Chưa có model local.' } }, 503)
                : json({ resource_version_id: 'version-test', index_run_id: 'run-test', index_status: 'PENDING', action: 'QUEUED' }, 202)
        }
        if (path.startsWith('/api/')) return json({ detail: 'fixture not found' }, 404)
        const target = path.startsWith('/assets/') ? resolve(root, 'dist', '.' + path) : join(root, 'dist/index.html')
        if (!target.startsWith(join(root, 'dist') + sep)) { res.writeHead(403); return res.end() }
        try {
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[extname(target)] || 'application/octet-stream' })
            res.end(await readFile(target))
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const profile = await mkdtemp(join(tmpdir(), 'etl-index-ui-'))
    const browser = spawn(browserPath, ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-extensions',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws
    const pending = new Map()
    let sequence = 0
    async function waitFor(fn) {
        for (let i = 0; i < 160; i++) { const result = await fn(); if (result) return result; await delay(100) }
        throw new Error('UI expectation timed out')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 5000)
            pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject: error => { clearTimeout(timer); reject(error) } })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
        if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
        return result.result.value
    }
    try {
        const port = await waitFor(async () => {
            try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
        })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const data = JSON.parse(event.data)
            const request = pending.get(data.id)
            if (request) { pending.delete(data.id); data.error ? request.reject(new Error(data.error.message)) : request.resolve(data.result) }
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        async function open() {
            await call('Page.navigate', { url: `${origin}/library/indexing?resourceId=resource-test&versionId=version-test` })
            await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Xây dựng chỉ mục'))"))
        }
        await open()
        assert.equal(postCount, 0)
        await waitFor(() => evaluate("document.body.textContent.includes('Danh sách Chunk (11)')"))
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent.trim() === 'Tiếp').click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Chunk 11') && !document.body.textContent.includes('Chunk 10')"))
        await evaluate("document.querySelector('details').open = true")
        assert.equal(await evaluate("document.querySelector('details').textContent.includes('Văn bản tiếng Việt')"), true)
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Xây dựng chỉ mục')).click()")
        await waitFor(() => evaluate("document.body.textContent.includes('run-test')"))
        assert.equal(postCount, 1)
        blocked = true
        await open()
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Xây dựng chỉ mục')).click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa có model local.')"))
        assert.equal(postCount, 2)
    } finally {
        if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' })); ws.close() }
        browser.kill()
        server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
        // Delete only the exact generated browser profile, never a user browser directory.
        if (profile.startsWith(join(tmpdir(), 'etl-index-ui-'))) await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }).catch(() => {})
    }
})

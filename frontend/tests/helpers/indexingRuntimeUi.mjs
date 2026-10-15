// Opt-in acceptance helper: proxy built React to the isolated REAL test backend.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { resolve, join, extname, sep } from 'node:path'

const root = process.cwd()
const backend = process.env.ETL_RUNTIME_API_URL
const resourceId = process.env.ETL_RUNTIME_RESOURCE_ID
const versionId = process.env.ETL_RUNTIME_VERSION_ID
assert.match(backend, /^http:\/\/127\.0\.0\.1:\d+$/)
const server = createServer(async (req, res) => {
    try {
        const path = new URL(req.url, backend).pathname
        if (path.startsWith('/api/') || path === '/health') {
            const upstream = await fetch(backend + req.url, { method: req.method })
            res.writeHead(upstream.status, { 'Content-Type': upstream.headers.get('content-type') || 'application/json' })
            return res.end(Buffer.from(await upstream.arrayBuffer()))
        }
        const target = path.startsWith('/assets/') ? resolve(root, 'frontend/dist', '.' + path) : join(root, 'frontend/dist/index.html')
        assert.ok(target.startsWith(join(root, 'frontend/dist') + sep))
        res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[extname(target)] || 'application/octet-stream' })
        res.end(await readFile(target))
    } catch { res.writeHead(500); res.end('Acceptance fixture unavailable') }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
const profile = await mkdtemp(join(tmpdir(), 'etl-real-model-ui-'))
const browser = spawn(process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
    '--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' })
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function waitFor(fn) {
    for (let i = 0; i < 150; i++) { const value = await fn(); if (value) return value; await delay(200) }
    throw new Error('Real runtime UI assertion timed out')
}
let ws, sequence = 0
const pending = new Map()
function call(method, params = {}) {
    const id = ++sequence
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout ${method}`)) }, 5000)
        pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject: error => { clearTimeout(timer); reject(error) } })
        ws.send(JSON.stringify({ id, method, params }))
    })
}
async function evaluate(expression) {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true })
    assert.equal(result.exceptionDetails, undefined)
    return result.result.value
}
async function status() { return (await fetch(`${backend}/api/v1/resource-versions/${versionId}/index-status`)).json() }
try {
    const initial = await status()
    assert.equal(initial.index_status, 'READY')
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
    await call('Page.navigate', { url: `${origin}/library/indexing?${new URLSearchParams({ resourceId, versionId })}` })
    await waitFor(() => evaluate("document.body.textContent.includes('Vector sẵn sàng') && document.body.textContent.includes('384') && document.body.textContent.includes('READY')"))
    assert.equal((await status()).latest_run_id, initial.latest_run_id, 'mount never POSTs')
    await waitFor(() => evaluate("Array.from(document.querySelectorAll('article h4')).filter(h => h.textContent.startsWith('Chunk ')).length === 10"))
    await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tiếp').click()")
    await waitFor(() => evaluate("document.body.textContent.includes('Chunk 11')"))
    await evaluate("document.querySelector('details').open = true")
    assert.ok(await evaluate("document.querySelector('details p').textContent.length > 0"))
    await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tái lập chỉ mục').click()")
    await waitFor(() => evaluate("document.querySelector('dialog')?.open"))
    await evaluate("Array.from(document.querySelectorAll('dialog button')).find(b => b.textContent === 'Hủy').click()")
    assert.equal((await status()).latest_run_id, initial.latest_run_id)
    await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tái lập chỉ mục').click()")
    await waitFor(() => evaluate("document.querySelector('dialog')?.open"))
    await evaluate("Array.from(document.querySelectorAll('dialog button')).find(b => b.textContent === 'Xác nhận').click()")
    let lastStatus
    const rebuilt = await waitFor(async () => {
        lastStatus = await status()
        if (lastStatus.index_status === 'FAILED') throw new Error(`Reindex failed: ${lastStatus.error_code}`)
        return lastStatus.index_status === 'READY' && lastStatus.latest_run_id !== initial.latest_run_id ? lastStatus : null
    }).catch(error => {
        throw new Error(`${error.message}; ${JSON.stringify({ index_status: lastStatus?.index_status,
            error_code: lastStatus?.error_code, new_run: lastStatus?.latest_run_id !== initial.latest_run_id })}`)
    })
    await waitFor(() => evaluate(`document.body.textContent.includes(${JSON.stringify(rebuilt.latest_run_id)}) && document.body.textContent.includes('ContentBag đã sẵn sàng.')`))
    console.log(JSON.stringify({ real_backend: true, real_model: true, index_status: rebuilt.index_status, chunks: rebuilt.chunk_count, dimension: rebuilt.embedding_dimension, ui: 'PASS', reindex_dialog: 'PASS' }))
} finally {
    if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' })); ws.close() }
    browser.kill()
    server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
    if (profile.startsWith(join(tmpdir(), 'etl-real-model-ui-'))) await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }).catch(() => {})
}

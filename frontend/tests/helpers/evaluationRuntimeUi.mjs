// Opt-in E2E: built React + REAL isolated evaluation/Search backend; independent fixture qrels.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { resolve, join, extname, sep } from 'node:path'

const backend = process.env.ETL_EVAL_API
const resource = process.env.ETL_EVAL_RESOURCE
assert.match(backend, /^http:\/\/127\.0\.0\.1:\d+$/)
assert.match(resource, /^[A-Za-z0-9_-]{1,30}$/)
const root = resolve('frontend/dist'), posts = [], reports = []
const server = createServer(async (request, response) => {
    try {
        const path = new URL(request.url, backend).pathname
        if (path.startsWith('/api/') || path === '/health') {
            if (request.method === 'POST') posts.push(path)
            if (path.endsWith('/report')) reports.push(request.url)
            const chunks = []; for await (const chunk of request) chunks.push(chunk)
            if (request.method === 'POST' && path.endsWith('/evaluations/runs')) await new Promise(resolve => setTimeout(resolve, 600))
            const upstream = await fetch(backend + request.url, { method: request.method,
                headers: { 'Content-Type': 'application/json', Accept: request.headers.accept || 'application/json' },
                ...(request.method === 'POST' ? { body: Buffer.concat(chunks) } : {}) })
            response.writeHead(upstream.status, { 'Content-Type': upstream.headers.get('content-type') || 'application/json' })
            if (upstream.headers.get('content-type')?.includes('application/x-ndjson')) {
                for await (const chunk of upstream.body) {
                    response.write(chunk)
                    await new Promise(resolve => setTimeout(resolve, 100))
                }
                return response.end()
            }
            return response.end(Buffer.from(await upstream.arrayBuffer()))
        }
        const target = path.startsWith('/assets/') ? resolve(root, '.' + path) : join(root, 'index.html')
        assert.ok(target.startsWith(root + sep))
        response.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[extname(target)] || 'application/octet-stream' })
        response.end(await readFile(target))
    } catch { response.writeHead(500); response.end('Isolated evaluation fixture unavailable') }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = `http://127.0.0.1:${server.address().port}`
const profile = await mkdtemp(join(tmpdir(), 'etl-evaluation-ui-'))
const browser = spawn(process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
    '--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' })
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function waitFor(fn) {
    for (let i = 0; i < 600; i++) { const value = await fn(); if (value) return value; await delay(100) }
    throw new Error('Evaluation UI acceptance timed out')
}
let ws, sequence = 0
const pending = new Map()
function call(method, params = {}) {
    const id = ++sequence
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout ${method}`)) }, 10000)
        pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject })
        ws.send(JSON.stringify({ id, method, params }))
    })
}
async function evaluate(expression) {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true })
    assert.equal(result.exceptionDetails, undefined)
    return result.result.value
}
const click = label => evaluate(`(Array.from(document.querySelectorAll('button')).find(e => e.textContent === ${JSON.stringify(label)}) || Array.from(document.querySelectorAll('a')).find(e => e.textContent === ${JSON.stringify(label)})).click()`)
async function input(selector, value) {
    await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); Object.getOwnPropertyDescriptor(el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(el, ${JSON.stringify(value)}); el.dispatchEvent(new Event('input', { bubbles: true })); })()`)
}
async function capture(name) {
    if (!process.env.ETL_EVAL_CAPTURE_DIR) return
    await mkdir(process.env.ETL_EVAL_CAPTURE_DIR, { recursive: true })
    const screenshot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
    await writeFile(join(process.env.ETL_EVAL_CAPTURE_DIR, name + '.png'), Buffer.from(screenshot.data, 'base64'))
}
try {
    const port = await waitFor(async () => {
        try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
    })
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
    ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
    ws.addEventListener('message', event => {
        const data = JSON.parse(event.data), p = pending.get(data.id)
        if (p) { pending.delete(data.id); if (data.error) p.reject(new Error(data.error.message)); else p.resolve(data.result) }
    })
    await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
    await call('Page.enable')
    await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false })
    await call('Browser.setDownloadBehavior', { behavior: 'deny' })
    await call('Page.navigate', { url: origin + '/experiments/evaluation-data' })
    await waitFor(() => evaluate("document.body.textContent.includes('Tạo dataset')"))
    assert.equal(await evaluate("document.querySelectorAll('nav[aria-label=\"Menu Thực nghiệm\"] a').length"), 3)
    assert.equal(await evaluate("document.querySelectorAll('nav[aria-label=\"Các bước thực nghiệm\"] a').length"), 0)
    assert.equal(posts.length, 0)
    await input('input:not([type])', 'SAMPLE isolated human fixture')
    await input('input[aria-label="Nội dung truy vấn 1"]', 'page')
    await input('textarea[aria-label="Nhãn truy vấn 1"]', resource + ':')
    await click('Tạo dataset')
    await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('grade')"))
    assert.equal(posts.length, 0)
    const annotation = join(profile, 'annotation.json')
    await writeFile(annotation, JSON.stringify({ name: 'SAMPLE isolated human fixture', rows: [{
        query_id: 'q01', query_text: 'page', resource_id: resource, resource_title: 'SAMPLE học liệu',
        candidate_description: 'Nội dung fixture độc lập', relevance: null,
    }] }))
    const documentRoot = await call('DOM.getDocument')
    const fileInput = await call('DOM.querySelector', { nodeId: documentRoot.root.nodeId, selector: 'input[type=file]' })
    await call('DOM.setFileInputFiles', { nodeId: fileInput.nodeId, files: [annotation] })
    await waitFor(() => evaluate("!!document.querySelector('select')"))
    assert.ok(await evaluate("document.body.textContent.includes('Đã chấm 0/1 học liệu')"))
    await click('Tạo dataset')
    await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('chưa chấm')"))
    assert.equal(posts.length, 0)
    await evaluate("(() => { const el = document.querySelector('select'); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, '3'); el.dispatchEvent(new Event('change', { bubbles: true })); })()")
    await waitFor(() => evaluate("document.body.textContent.includes('Đã chấm 1/1 học liệu')"))
    assert.equal(await evaluate("document.querySelector('input[type=checkbox]')"), null)
    await capture('dataset-import')
    await click('Tạo dataset')
    await waitFor(() => evaluate("document.body.textContent.includes('Dataset ID:')"))
    assert.equal(posts.filter(path => path.endsWith('/datasets')).length, 1)
    await click('Chạy thực nghiệm')
    await waitFor(() => evaluate("document.querySelector('input')?.value.length === 26"))
    assert.equal(posts.filter(path => path.endsWith('/runs')).length, 0)
    await click('Chạy thực nghiệm')
    await waitFor(() => evaluate("!!document.querySelector('progress')"))
    assert.equal(await evaluate("document.querySelector('progress').hasAttribute('value')"), true)
    await waitFor(() => evaluate("document.querySelector('progress')?.value > 0"))
    assert.equal(await evaluate("document.querySelector('progress').max"), 100)
    assert.equal(await evaluate("getComputedStyle(document.querySelector('progress')).height"), '20px')
    assert.equal(await evaluate("getComputedStyle(document.querySelector('progress')).accentColor"), 'rgb(37, 99, 235)')
    assert.equal(await evaluate("document.querySelector('fieldset').disabled"), true)
    await capture('run-progress')
    await waitFor(() => evaluate("document.body.textContent.includes('Trạng thái: COMPLETED')"))
    assert.equal(posts.filter(path => path.endsWith('/runs')).length, 1)
    const runId = await evaluate("new URLSearchParams(location.search).get('run_id')")
    assert.ok(await evaluate("document.querySelector('table').textContent.includes('0.2000')"))
    assert.ok(await evaluate("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Xuất báo cáo CSV')"), 'export is available alongside results')
    await call('Page.reload')
    await waitFor(() => evaluate("document.body.textContent.includes('Trạng thái: COMPLETED')"))
    assert.equal(posts.filter(path => path.endsWith('/runs')).length, 1, 'refresh never runs a benchmark')
    await evaluate("document.querySelector('table').scrollIntoView({ block: 'center' })")
    await capture('results-desktop')
    await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
    assert.equal(await evaluate("document.querySelector('main').scrollWidth <= document.querySelector('main').clientWidth"), true)
    await capture('results-mobile')
    await call('Page.navigate', { url: origin + `/experiments/reports?run_id=${runId}` })
    await waitFor(() => evaluate("document.body.textContent.includes('Xuất báo cáo CSV')"))
    assert.equal(await evaluate("location.pathname"), '/experiments/results', 'legacy report link retains run ID in combined results page')
    assert.equal(await evaluate("new URLSearchParams(location.search).get('run_id')"), runId)
    await click('Xuất báo cáo CSV')
    await waitFor(() => reports.some(path => path.endsWith('format=csv')))
    await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tải JSON')?.disabled === false"))
    await click('Tải JSON')
    await waitFor(() => reports.some(path => path.endsWith('format=json')))
    const csv = await fetch(`${backend}/api/v1/evaluations/runs/${runId}/report`)
    const bytes = new Uint8Array(await csv.arrayBuffer())
    assert.deepEqual([...bytes.slice(0, 3)], [239,187,191])
    assert.ok(new TextDecoder().decode(bytes).includes('0.2'))
    const saved = await (await fetch(`${backend}/api/v1/evaluations/runs/${runId}`)).json()
    assert.equal(saved.status, 'COMPLETED')
    assert.equal(saved.queries.length, 1)
    assert.equal(saved.summary.bow.metrics['5'].precision.value, .2)
    console.log(JSON.stringify({ ui: 'PASS', backend: 'isolated-real-application', methods: 3, queries: 1,
        manual_fixture: true, refresh: 'PASS', csv_bom: 'PASS', routes: 4, production_data_used: false }))
} finally {
    if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' })); ws.close() }
    browser.kill(); server.closeAllConnections()
    await new Promise(resolve => server.close(resolve))
    if (profile.startsWith(join(tmpdir(), 'etl-evaluation-ui-'))) await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }).catch(() => {})
}

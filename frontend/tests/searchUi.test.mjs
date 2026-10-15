// Built React pages exercised in Edge against isolated HTTP fixtures; no Dev data.
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
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('search form, methods, results, pagination, empty, error/retry and mobile layout', {
    skip: !existsSync(executable) || !existsSync(join(root, 'dist/index.html')), timeout: 60000,
}, async () => {
    let postCount = 0, mode = 'results'
    const bodies = []
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost')
        const json = (body, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)) }
        if (url.pathname === '/health') return json({ status: 'ok', database: 'ok' })
        if (['/api/v1/subjects', '/api/v1/grade-levels', '/api/v1/resource-types'].includes(url.pathname)) {
            return json({ items: url.pathname.endsWith('subjects') ? [{ id: 's1', name: 'Toán', grade_level_id: 'g1' }] :
                url.pathname.endsWith('grade-levels') ? [{ id: 'g1', grade_name_vi: 'Lớp 6' }] : [{ code: 'DOC', name: 'Tài liệu' }],
                pagination: { page: 1, total_pages: 1 } })
        }
        if (url.pathname === '/api/v1/search') {
            postCount++
            let raw = ''
            for await (const part of req) raw += part
            const body = JSON.parse(raw)
            bodies.push(body)
            if (mode === 'error') return json({ detail: { code: 'BLOCKED_EMBEDDING_RUNTIME', message: 'Model chưa sẵn sàng.' } }, 503)
            if (mode === 'empty') return json({ ...body, items: [], total: 0, total_pages: 0, message: 'NO_READY_INDEX', ranking_algorithm: 'rrf-v1' })
            return json({ ...body, total: 2, total_pages: 2, message: 'OK', ranking_algorithm: 'rrf-v1', items: [{
                resource_id: `r${body.page}`, resource_title: `Học liệu ${body.page}`, resource_version_id: 'v1', version_number: 1,
                provider_name: 'Nhà cung cấp', resource_type_code: 'DOC', bag_id: 'b1', representation_mode: 'fallback',
                score: .03, score_components: { bow: .5, vector: .7 }, matched_bag_count: 1, matched_keywords: ['toán'],
                snippet: 'Văn bản <script>không được thực thi</script>', page_from: 2, page_to: 2, content_unit_id: 'unit-1', context_id: 'token-1',
            }] })
        }
        if (url.pathname.startsWith('/api/')) return json({ detail: 'not found' }, 404)
        const file = url.pathname.startsWith('/assets/') ? resolve(root, 'dist', '.' + url.pathname) : join(root, 'dist/index.html')
        if (!file.startsWith(join(root, 'dist') + sep)) { res.writeHead(403); return res.end() }
        try {
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[extname(file)] || 'application/octet-stream' })
            res.end(await readFile(file))
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const profile = await mkdtemp(join(tmpdir(), 'etl-search-ui-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map()
    async function waitFor(fn) {
        for (let i = 0; i < 150; i++) { const result = await fn(); if (result) return result; await delay(100) }
        throw new Error('Search UI assertion timed out')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 5000)
            pending.set(id, { resolve: value => { clearTimeout(timeout); resolve(value) }, reject })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(result.exceptionDetails, undefined)
        return result.result.value
    }
    const click = label => evaluate(`Array.from(document.querySelectorAll('button,a')).find(e => e.textContent === ${JSON.stringify(label)}).click()`)
    async function open(path) { await call('Page.navigate', { url: origin + path }) }
    try {
        const port = await waitFor(async () => {
            try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
        })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const message = JSON.parse(event.data), item = pending.get(message.id)
            if (item) { pending.delete(message.id); if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result) }
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false })
        await open('/search/query-methods')
        await waitFor(() => evaluate("document.body.textContent.includes('Chọn BoW')"))
        await click('Chọn BoW')
        await waitFor(() => evaluate("document.querySelector('#search-method')?.value === 'bow'"))
        assert.equal(postCount, 0)
        await waitFor(() => evaluate("document.querySelector('#search-grade')?.disabled === false"))
        await evaluate("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(document.querySelector('#search-query'), 'toán'); document.querySelector('#search-query').dispatchEvent(new Event('input', { bubbles: true }))")
        await evaluate("document.querySelector('#search-grade').value='g1'; document.querySelector('#search-grade').dispatchEvent(new Event('change', { bubbles: true }))")
        await evaluate("document.querySelector('form').requestSubmit()")
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        assert.equal(postCount, 1, 'only one POST on submission, including React StrictMode')
        assert.equal(bodies[0].method, 'bow')
        assert.equal(bodies[0].filters.grade_level_id, 'g1')
        assert.equal(await evaluate("document.querySelector('article script')"), null)
        await click('Tiếp')
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 2')"))
        assert.equal(bodies.at(-1).page, 2)
        await evaluate('history.back()')
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        await click('Sửa tìm kiếm')
        await waitFor(() => evaluate("document.querySelector('#search-query')?.value === 'toán'"))
        assert.equal(await evaluate("document.querySelector('#search-grade').value"), 'g1')
        mode = 'error'
        await open('/search/result?q=toán&method=vector')
        await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('Model chưa sẵn sàng')"))
        assert.equal(await evaluate("getComputedStyle(document.querySelector('[role=alert]')).color"), 'rgb(185, 28, 28)')
        mode = 'empty'
        await click('Thử lại')
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa có học liệu được lập chỉ mục')"))
        assert.equal(await evaluate("!!document.querySelector('a[href=\"/library/indexing\"]')"), true)
        mode = 'results'
        await open('/search/result?q=toán&method=hybrid')
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate("document.querySelector('article').getBoundingClientRect().width <= innerWidth"), true)
    } finally {
        if (ws) ws.close()
        browser.kill()
        await new Promise(resolve => server.close(resolve))
        await delay(400)
        await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
    }
})

// Real built React UI in headless Edge; isolated HTTP fixtures, no live API/DB.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, extname, join, resolve, sep } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const dist = resolve(process.env.ETL_TEST_DIST || join(root, 'dist'))
const browserPath = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('index UI selects a version, never POSTs on mount, queues and displays runtime errors', {
    skip: !existsSync(browserPath) || !existsSync(join(dist, 'index.html')),
    timeout: 60000,
}, async () => {
    let postCount = 0
    let blocked = false
    let statusReads = 0
    let chunkReads = 0
    let activeRun = null
    let pendingReads = 0
    let reindexCount = 0
    let blockStatus = false
    let blockCode = 'BLOCKED_EMBEDDING_RUNTIME'
    let structureBlocked = false
    let waitingForOcr = false
    let ocrReads = 0
    let forcedState = null
    let autoEnabled = false
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost')
        const path = url.pathname
        function json(body, status = 200) { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)) }
        if (path === '/health') return json({ status: 'ok', database: 'ok' })
        if (path === '/api/v1/auth/config') return json({ mode: 'legacy_local' })
        if (path === '/api/v1/resources') return json({ items: [{ id: 'resource-test', title: 'Học liệu kiểm thử' }], pagination: { total: 1 } })
        if (path === '/api/v1/resources/resource-test/versions') return json([
            { id: 'version-test', resource_id: 'resource-test', version_number: 1 },
            { id: 'version-two', resource_id: 'resource-test', version_number: 2 },
        ])
        if (path.endsWith('/index-status')) {
            statusReads++
            const second = path.includes('version-two')
            const state = forcedState ?? (waitingForOcr && ++ocrReads < 2 ? 'CONTENT_NOT_READY' : structureBlocked ? 'BLOCKED_STRUCTURE' : blockStatus ? 'BLOCKED_RUNTIME' : !second && activeRun ? (++pendingReads < 2 ? 'PENDING' : 'READY') : 'NOT_STARTED')
            return json({ resource_version_id: second ? 'version-two' : 'version-test', processing_status: state === 'CONTENT_NOT_READY' ? 'OCR_PENDING' : 'CONTENT_READY',
                index_status: state, representation_mode: state === 'BLOCKED_STRUCTURE' ? 'unavailable' : 'fallback', auto_enabled: autoEnabled, bag_count: state === 'READY' ? 11 : 0,
                bags: state === 'READY' && url.searchParams.get('include_bags') !== 'false' ? Array.from({ length: 11 }, (_, i) => ({ id: `root-test-${i}`, title: `Test Bag ${i + 1}`, unit_type: 'PAGE_TEXT', representation_mode: 'fallback', keywords: ['toán'], embedding_text: 'toán', embedding_model: 'ui-test-only@1', embedding_dimension: 3, vector_ready: true }))
                    .slice(Number(url.searchParams.get('bag_offset') || 0), Number(url.searchParams.get('bag_offset') || 0) + Number(url.searchParams.get('bag_limit') || 100)) : [],
                ...(url.searchParams.has('bag_limit') ? { bag_pagination: { offset: Number(url.searchParams.get('bag_offset') || 0), limit: 10, total: 11 }, bag_revision: 'd'.repeat(64) } : {}),
                latest_run_id: second ? null : activeRun, published_run_id: state === 'READY' ? activeRun : null, chunk_count: 0,
                error_code: state === 'BLOCKED_STRUCTURE' ? 'BAG_STRUCTURE_UNAVAILABLE' : state === 'BLOCKED_RUNTIME' || state === 'FAILED' ? blockCode : null,
                bow_ready: state === 'READY', vector_ready: state === 'READY', embedding_model: 'ui-test-only', embedding_dimension: 3,
                message: state === 'BLOCKED_RUNTIME' ? 'Model chưa sẵn sàng.' : state === 'PENDING' ? 'Đang chờ xử lý.' : state === 'READY' ? 'Chỉ mục sẵn sàng.' : 'Chưa lập chỉ mục.' })
        }
        if (path.endsWith('/chunks')) {
            chunkReads++
            if (path.includes('version-two') || waitingForOcr && ocrReads < 2) return json({ items: [], total: 0, index_run_id: null, index_status: waitingForOcr ? 'CONTENT_NOT_READY' : 'PREVIEW', representation_status: waitingForOcr ? 'CONTENT_NOT_READY' : 'PREVIEW', chunk_storage: 'transient', offset: 0, limit: 10 })
            const offset = Number(new URL(req.url, 'http://localhost').searchParams.get('offset'))
            return json({ resource_version_id: 'version-test', index_run_id: null, index_status: 'PREVIEW', representation_status: 'PREVIEW', chunk_storage: 'transient', total: 11, offset, limit: 10,
                items: Array.from({ length: offset === 0 ? 10 : 1 }, (_, i) => ({ id: `chunk-${offset + i + 1}`, content_unit_id: 'unit-test',
                    chunk_sequence: offset + i + 1, unit_chunk_sequence: 1, page_from: 2, page_to: 2,
                    char_start: 0, char_end: 17, text: 'Văn bản tiếng Việt', review_status: 'UNREVIEWED', retrieval_eligible: false,
                    leaf_keywords: ['toán'] })) })
        }
        if (path.endsWith('/index') && req.method === 'POST') {
            postCount++
            if (!blocked) { activeRun = 'run-test'; pendingReads = 0 }
            return blocked ? json({ detail: { code: 'BLOCKED_EMBEDDING_RUNTIME', message: 'Model chưa sẵn sàng.' } }, 503)
                : json({ resource_version_id: 'version-test', index_run_id: 'run-test', index_status: 'PENDING', action: 'QUEUED' }, 202)
        }
        if (path.endsWith('/reindex') && req.method === 'POST') {
            reindexCount++
            activeRun = 'rebuild-test'
            pendingReads = 0
            return json({ resource_version_id: 'version-test', index_run_id: activeRun, index_status: 'PENDING', action: 'QUEUED' }, 202)
        }
        if (path.startsWith('/api/')) return json({ detail: 'fixture not found' }, 404)
        const target = path.startsWith('/assets/') ? resolve(dist, '.' + path) : join(dist, 'index.html')
        if (!target.startsWith(dist + sep)) { res.writeHead(403); return res.end() }
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
            if (request) {
                pending.delete(data.id)
                if (data.error) request.reject(new Error(data.error.message))
                else request.resolve(data.result)
            }
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false })
        async function tab(label) {
            await evaluate(`Array.from(document.querySelectorAll('nav[aria-label="Phân đoạn và chỉ mục"] a')).find(a => a.textContent === ${JSON.stringify(label)}).click()`)
        }
        async function capture(name) {
            if (!process.env.ETL_TEST_CAPTURE_LAYOUT) return
            const folder = resolve(root, '../.test-tmp/indexing-layout')
            await mkdir(folder, { recursive: true })
            const shot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
            await writeFile(join(folder, name + '.png'), Buffer.from(shot.data, 'base64'))
        }
        async function open() {
            await call('Page.navigate', { url: `${origin}/library/indexing?resourceId=resource-test&versionId=version-test` })
            await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Xây dựng chỉ mục'))"))
        }
        await open()
        assert.equal(postCount, 0)
        await waitFor(() => evaluate("document.body.textContent.includes('Không cấu trúc (Fallback)')"))
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Xây dựng chỉ mục') && !b.disabled)"))
        assert.equal(chunkReads, 0, 'closed chunks tab does not fetch content')
        await tab('Phân đoạn')
        await waitFor(() => evaluate("document.body.textContent.includes('Danh sách Chunk (11)')"))
        assert.equal(await evaluate("document.querySelector('[data-index-panel=chunks]').hidden"), false)
        assert.equal(await evaluate("document.querySelector('[data-index-panel=index]').hidden"), true)
        assert.equal(await evaluate("new URL(location.href).searchParams.get('view')"), 'chunks')
        assert.equal(await evaluate("new URL(location.href).searchParams.get('resourceId')"), 'resource-test')
        await capture('chunks-desktop')
        await evaluate("Array.from(document.querySelectorAll('[data-index-panel=chunks] button')).find(b => b.textContent.trim() === 'Tiếp').click()")
        await waitFor(() => evaluate("document.querySelector('[data-index-panel=chunks] tbody tr td')?.textContent === '#11' && document.querySelectorAll('[data-index-panel=chunks] tbody tr').length === 1"))
        assert.equal(await evaluate("document.querySelector('[data-index-panel=chunks] tbody tr td:nth-child(4)').textContent.includes('Văn bản tiếng Việt')"), true)
        await tab('Chỉ mục')
        await tab('Phân đoạn')
        // The existing conditional mount discards the old page on collapse.
        // Reopening must reauthorize and load the first page, not replay old rows.
        await waitFor(() => evaluate("document.querySelector('[data-index-panel=chunks] tbody tr td')?.textContent === '#1'"))
        await tab('Chỉ mục')
        assert.equal(postCount, 0, 'switching views never POSTs')
        await evaluate("const buildButton = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Xây dựng chỉ mục')); buildButton.click(); buildButton.click()")
        await waitFor(() => evaluate("document.body.textContent.includes('run-test')"))
        assert.equal(postCount, 1)
        await waitFor(() => evaluate("document.querySelector('[data-index-panel=index] tbody tr:nth-child(2) td')?.textContent === 'Đang chờ'"))
        await waitFor(() => evaluate("document.body.textContent.includes('Đang chờ xử lý.')"))
        const readCount = statusReads
        await waitFor(async () => { await delay(200); return statusReads > readCount })
        await waitFor(() => evaluate("document.body.textContent.includes('Chỉ mục sẵn sàng.')"))
        assert.equal(await evaluate("document.querySelector('[data-index-panel=index] tbody tr:nth-child(2) td')?.textContent"), 'Sẵn sàng')
        await capture('index-desktop')
        await tab('ContentBag')
        await waitFor(() => evaluate("document.querySelectorAll('[data-index-panel=bags] tbody tr').length === 10"))
        assert.equal(await evaluate("document.querySelectorAll('[data-index-panel=bags] tbody tr').length"), 10)
        await evaluate("Array.from(document.querySelectorAll('[data-index-panel=bags] button')).find(b => b.textContent === 'Tiếp').click()")
        await waitFor(() => evaluate("document.querySelectorAll('[data-index-panel=bags] tbody tr').length === 1"))
        await evaluate("Array.from(document.querySelectorAll('[data-index-panel=bags] button')).find(b => b.textContent === 'Trước').click()")
        await capture('bags-desktop')
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        await capture('bags-mobile')
        assert.equal(await evaluate("document.querySelector('[data-index-panel=bags]').getBoundingClientRect().width <= innerWidth"), true)
        await tab('Chỉ mục')
        await capture('index-mobile')
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false })
        const terminalReads = statusReads
        await delay(4300)
        assert.equal(statusReads, terminalReads, 'polling stops at READY')
        blocked = true
        activeRun = null
        await open()
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Xây dựng chỉ mục') && !b.disabled)"))
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Xây dựng chỉ mục')).click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Model chưa sẵn sàng.')"))
        assert.equal(postCount, 2)
        assert.match(await evaluate("document.querySelector('[role=alert]')?.textContent"), /Model chưa sẵn sàng/ )
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tái lập chỉ mục').click()")
        await waitFor(() => evaluate("document.querySelector('dialog')?.open"))
        await evaluate("Array.from(document.querySelectorAll('dialog button')).find(b => b.textContent === 'Hủy').click()")
        assert.equal(reindexCount, 0)
        assert.equal(await evaluate("document.querySelector('dialog')"), null)
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tái lập chỉ mục').click()")
        await waitFor(() => evaluate("document.querySelector('dialog')?.open"))
        assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('dialog button')).map(b => b.textContent)"), ['Xác nhận', 'Hủy'])
        await capture('confirm-desktop')
        await evaluate("Array.from(document.querySelectorAll('dialog button')).find(b => b.textContent === 'Xác nhận').click()")
        await waitFor(() => evaluate("document.body.textContent.includes('rebuild-test')"))
        assert.equal(reindexCount, 1)
        blockStatus = true
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tải lại trạng thái').click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Model chưa sẵn sàng.')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tái lập chỉ mục').disabled"), true)
        blockCode = 'BLOCKED_INDEX_SCHEMA'
        const beforeSchemaPosts = postCount
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tải lại trạng thái').click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Schema MongoDB chưa hợp lệ')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Xây dựng chỉ mục').disabled"), true)
        assert.equal(await evaluate("document.querySelectorAll('details').length > 0"), true)
        assert.equal(postCount, beforeSchemaPosts)
        blockStatus = false
        activeRun = null
        await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tải lại trạng thái').click()")
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Xây dựng chỉ mục' && !b.disabled)"))
        assert.equal(postCount, beforeSchemaPosts, 'schema refresh never implicitly enqueues')
        blockStatus = false
        structureBlocked = true
        await open()
        await waitFor(() => evaluate("document.body.textContent.includes('BLOCKED_STRUCTURE')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Xây dựng chỉ mục')).disabled"), true)
        assert.equal(await evaluate("document.querySelectorAll('details').length > 0"), true)
        structureBlocked = false
        waitingForOcr = true
        activeRun = null
        const beforeWaitPosts = postCount
        await open()
        await waitFor(() => evaluate("document.body.textContent.includes('Đang chờ nội dung.')"))
        const hiddenReads = statusReads
        await evaluate("Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange'))")
        await delay(4200)
        assert.equal(statusReads, hiddenReads, 'hidden tab pauses polling')
        await evaluate("delete document.hidden; document.dispatchEvent(new Event('visibilitychange'))")
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(b => b.textContent === 'Xây dựng chỉ mục' && !b.disabled)"))
        await tab('Phân đoạn')
        await waitFor(() => evaluate("document.body.textContent.includes('Danh sách Chunk (11)')"))
        await tab('Chỉ mục')
        assert.equal(postCount, beforeWaitPosts, 'OCR completion never triggers an implicit POST')
        waitingForOcr = false
        forcedState = 'FAILED'
        await open()
        await waitFor(() => evaluate("document.body.textContent.includes('FAILED')"))
        assert.equal(await evaluate("document.body.textContent.includes('Schema MongoDB chưa hợp lệ')"), false, 'historical schema failure is not a current runtime blocker')
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tái lập chỉ mục').disabled"), false)
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Xây dựng chỉ mục').disabled"), true)
        blockCode = 'LEAF_KEYWORDS_UNAVAILABLE'
        await open()
        await waitFor(() => evaluate("document.body.textContent.includes('FAILED')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(b => b.textContent === 'Tái lập chỉ mục').disabled"), false, 'historical keyword failure must not block a recovered root')
        forcedState = 'STALE'
        autoEnabled = true
        await open()
        await waitFor(() => evaluate("document.body.textContent.includes('STALE') && document.body.textContent.includes('Chỉ mục cần cập nhật.')"))
        assert.equal(postCount, beforeWaitPosts)
        forcedState = null
        autoEnabled = false
        await evaluate("const select = document.querySelector('#index-version'); select.value = 'version-two'; select.dispatchEvent(new Event('change', { bubbles: true }))")
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa lập chỉ mục.') && !document.body.textContent.includes('rebuild-test')"))
        assert.equal(await evaluate("document.querySelectorAll('[data-index-panel=chunks] tbody tr').length"), 0, 'previous version chunks are not rendered')
    } finally {
        if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' })); ws.close() }
        browser.kill()
        server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
        // Delete only the exact generated browser profile, never a user browser directory.
        if (profile.startsWith(join(tmpdir(), 'etl-index-ui-'))) await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }).catch(() => {})
    }
})

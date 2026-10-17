// Real temporary Edge profile against read-only HTTP fixtures, never Dev.
import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { resolve, join, extname, sep, dirname, basename } from 'node:path'

const root = resolve('frontend/dist')
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
test('Master Data read-only and file readiness/errors in real browser', {
    skip: !existsSync(executable) || !existsSync(join(root, 'index.html')), timeout: 90000,
}, async () => {
    let ready = false, fileStatus = 200, fileRequests = 0, writes = 0
    const server = createServer(async (req, res) => {
        const path = new URL(req.url, 'http://127.0.0.1').pathname
        const json = (body, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)) }
        if (req.method !== 'GET') { writes++; return json({}, 403) }
        if (path === '/health') return json({ status: 'ok', database: 'ok' })
        if (path === '/api/v1/master-data/sync/status') return json({
            file_sync_supported: true, file_sync_error: null, file_worker_healthy: true,
            auto_supported: true, auto_enabled: false, interval_seconds: 300, worker_healthy: true,
            auto_running: false, last_auto_run_at: null, next_run_at: null, auto_error: null,
            auto_all_enabled: false, auto_streams: [], runs: [],
        })
        if (path === '/api/v1/master-data/sync/files') {
            const params = new URL(req.url, 'http://127.0.0.1').searchParams
            assert.equal(params.get('size'), '7')
            const after = params.get('after')
            return json({
            items: Array.from({ length: after ? 1 : 7 }, (_, i) => ({
                source_version_id: 'source-v' + (after ? 8 : i + 1), resource_id: 'r1', resource_title: after ? 'Nguồn trang 2' : 'Nguồn kiểm thử',
                source_version_number: 1, source_lifecycle_status: 'APPROVED', updated_at: '2026-01-01',
                import_status: 'IMPORTED', retry_eligible: false, import_job_id: 'i1',
                etl_status: 'CONTENT_READY', retry_count: 1, last_error_code: null,
                target_version_id: 'v1', processing_job_id: 'j1' })),
            next_cursor: after ? null : 'page-2', file_sync_supported: true, file_sync_error: null, file_worker_healthy: true,
            })
        }
        const options = {
            '/api/v1/providers': Array.from({ length: 9 }, (_, i) => ({ id: 'p' + i, name: 'Đơn vị ' + i, provider_type: 'INTERNAL' })),
            '/api/v1/resource-types': [{ code: 'DOC', name: 'Tài liệu' }],
            '/api/v1/grade-levels': [{ id: 'g1', grade_code: '6', grade_name_vi: 'Lớp 6' }],
            '/api/v1/subjects': [{ id: 's1', name: 'Toán', grade_level_id: 'g1' }],
        }
        if (path in options) {
            const params = new URL(req.url, 'http://127.0.0.1').searchParams
            const page = Number(params.get('page') || 1), size = Number(params.get('size') || 100)
            const query = (params.get('q') || '').toLocaleLowerCase('vi')
            const items = options[path].filter(row => Object.values(row).join(' ').toLocaleLowerCase('vi').includes(query))
            return json({ items: items.slice((page - 1) * size, page * size),
                pagination: { page, size, total: items.length, total_pages: Math.ceil(items.length / size) } })
        }
        if (/^\/api\/v1\/resources\/r[01]$/.test(path)) return json({
            id: path.endsWith('r0') ? 'r0' : 'r1', title: 'Học liệu kiểm thử', source_origin: 'POSTGRESQL',
            provider_name: 'Đơn vị 1', resource_type_name_vi: 'Tài liệu', publication_status: 'PUBLISHED',
            is_active: true, subjects: [], grade_levels: [], created_at: '2026-01-01', updated_at: '2026-01-01',
        })
        if (path === '/api/v1/resources/r0/versions') return json([])
        if (path === '/api/v1/resources/r1/versions') return json([{ id: 'v1', version_number: 1, created_at: '2026-01-01' }])
        if (path === '/api/v1/resource-versions/v1/index-status') return json({ processing_status: 'CONTENT_READY', index_status: ready ? 'READY' : 'NOT_STARTED' })
        if (path === '/api/v1/resource-versions/v1/file') {
            fileRequests++
            if (fileStatus !== 200) return json({ detail: { code: 'FILE_NOT_READY' } }, fileStatus)
            res.writeHead(200, { 'Content-Type': 'image/png' })
            return res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=', 'base64'))
        }
        if (path.startsWith('/api/')) return json({}, 404)
        const target = path.startsWith('/assets/') ? resolve(root, '.' + path) : join(root, 'index.html')
        assert.ok(target.startsWith(root + sep))
        res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[extname(target)] || 'application/octet-stream' })
        res.end(await readFile(target))
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = 'http://127.0.0.1:' + server.address().port
    const profile = await mkdtemp(join(tmpdir(), 'etl-phase5-ui-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions',
        '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'],
        { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map()
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout ' + method)) }, 10000)
            pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(result.exceptionDetails, undefined)
        return result.result.value
    }
    async function waitFor(fn) {
        for (let i = 0; i < 200; i++) { const value = await fn(); if (value) return value; await delay(100) }
        throw new Error('Phase05 browser assertion timeout')
    }
    const click = label => evaluate('(Array.from(document.querySelectorAll("button")).find(e => e.textContent === ' + JSON.stringify(label) + ')).click()')
    const open = path => call('Page.navigate', { url: origin + path })
    async function capture(name) {
        const folder = resolve('.test-tmp/phase5-ui')
        await mkdir(folder, { recursive: true })
        const shot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
        await writeFile(join(folder, name + '.png'), Buffer.from(shot.data, 'base64'))
    }
    try {
        const port = await waitFor(async () => {
            try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
        })
        const pages = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const data = JSON.parse(event.data), item = pending.get(data.id)
            if (item) {
                pending.delete(data.id)
                if (data.error) item.reject(new Error(data.error.message))
                else item.resolve(data.result)
            }
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false })
        await open('/systems/master-data')
        await waitFor(() => evaluate('document.body?.textContent.includes("Trang 1/2")'))
        assert.equal(await evaluate('document.querySelectorAll("tbody tr").length'), 7)
        await click('Sau')
        await waitFor(() => evaluate('document.body?.textContent.includes("Trang 2/2")'))
        assert.equal(await evaluate('document.querySelectorAll("tbody tr").length'), 2)
        await capture('master-desktop')
        await evaluate('document.querySelector("input").focus()')
        await call('Input.insertText', { text: 'Đơn vị 8' })
        await click('Lọc')
        await waitFor(() => evaluate('document.querySelectorAll("tbody tr").length===1 && document.querySelector("tbody").textContent.includes("Đơn vị 8")'))
        await evaluate('const el=document.querySelector("select");el.value="subjects";el.dispatchEvent(new Event("change",{bubbles:true}))')
        await waitFor(() => evaluate('document.querySelector("tbody")?.textContent.includes("Toán")'))
        assert.equal(await evaluate('document.querySelector("form").method'), 'get')
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        await capture('master-mobile')
        assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 960, deviceScaleFactor: 1, mobile: false })
        await open('/library/source-sync')
        await waitFor(() => evaluate('document.querySelector("h1")?.textContent==="Đồng bộ dữ liệu nguồn"'))
        assert.equal(await evaluate('!!document.querySelector("section[aria-label=\\"Tệp học liệu từ nguồn\\"]")'), false)
        await evaluate('Array.from(document.querySelectorAll("a")).find(e=>e.textContent==="Tệp học liệu từ nguồn").click()')
        await waitFor(() => evaluate('Array.from(document.querySelectorAll("summary")).some(e=>e.textContent==="Nguồn / đích")'))
        assert.equal(await evaluate('location.pathname'), '/library/source-sync/files')
        assert.equal(await evaluate('Array.from(document.querySelectorAll("nav[aria-label=\\"Menu Kho học liệu\\"] a")).some(e=>e.textContent==="Tệp học liệu từ nguồn")'), false)
        assert.equal(await evaluate('document.querySelectorAll("tbody tr").length'), 7)
        await click('Sau')
        await waitFor(() => evaluate('document.body?.textContent.includes("Trang 2") && document.querySelectorAll("tbody tr").length===1'))
        assert.equal(await evaluate('document.querySelector("tbody td > span")?.textContent'), 'Nguồn trang 2')
        await click('Trước')
        await waitFor(() => evaluate('document.body?.textContent.includes("Trang 1") && document.querySelectorAll("tbody tr").length===7'))
        assert.equal(await evaluate('document.querySelector("tbody td > span")?.textContent'), 'Nguồn kiểm thử')
        assert.equal(await evaluate('Array.from(document.querySelectorAll("tbody a")).some(e=>e.textContent==="Nguồn kiểm thử")'), false)
        assert.ok(await evaluate('document.querySelector("tbody tr").getBoundingClientRect().height < 90'))
        await capture('source-files-compact')
        await evaluate('Array.from(document.querySelectorAll("summary")).find(e=>e.textContent==="Nguồn / đích").click()')
        await waitFor(() => evaluate('document.body?.textContent.includes("SOURCE: source-v1")'))
        const processing = await evaluate('Array.from(document.querySelectorAll("a")).find(a=>a.textContent==="Xem xử lý").href')
        const processingUrl = new URL(processing)
        assert.equal(processingUrl.pathname, '/library/processing')
        assert.equal(processingUrl.searchParams.get('resourceId'), 'r1')
        assert.equal(processingUrl.searchParams.get('versionId'), 'v1')
        assert.equal(processingUrl.searchParams.get('jobId'), 'j1')
        await capture('source-target')
        await open('/library/r0')
        await waitFor(() => evaluate('document.body?.textContent.includes("Chưa nhập tệp từ nguồn")'))
        assert.equal(await evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent==="Xem tệp").disabled'), true)
        await open('/library/r1')
        await waitFor(() => evaluate('document.body?.textContent.includes("Chỉ mục chưa sẵn sàng")'))
        assert.equal(await evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent==="Xem tệp").disabled'), true)
        assert.equal(fileRequests, 0)
        ready = true
        await click('Kiểm tra lại')
        await waitFor(() => evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent==="Xem tệp")?.disabled === false'))
        await click('Xem tệp')
        await waitFor(() => evaluate('!!document.querySelector("img[alt=\\"Nội dung tệp học liệu\\"]")'))
        await capture('file-ready')
        for (const [status, text] of [[403, 'chưa có quyền xem'], [404, 'Không tìm thấy tệp'], [409, 'Tệp chưa sẵn sàng'], [502, 'Dịch vụ tệp đang gián đoạn']]) {
            fileStatus = status
            await open('/library/r1')
            await waitFor(() => evaluate('Array.from(document.querySelectorAll("button")).find(e=>e.textContent==="Xem tệp")?.disabled === false'))
            await click('Xem tệp')
            await waitFor(() => evaluate('Array.from(document.querySelectorAll("[role=alert]")).some(e=>e.textContent.includes(' + JSON.stringify(text) + '))'))
        }
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        await capture('file-error-mobile')
        assert.equal(await evaluate('!!Array.from(document.querySelectorAll("a")).find(a=>a.href.includes("drive.google.com"))'), false)
        assert.equal(writes, 0)
    } finally {
        if (ws?.readyState === WebSocket.OPEN) { ws.send(JSON.stringify({ id: ++sequence, method: 'Browser.close' })); ws.close() }
        browser.kill()
        server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
        assert.equal(dirname(profile), tmpdir())
        assert.ok(basename(profile).startsWith('etl-phase5-ui-'))
        await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }).catch(() => {})
    }
})


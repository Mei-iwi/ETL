// Isolated built UI and API fixtures; no business database connections.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { extname, join, resolve, sep } from 'node:path'
import test from 'node:test'

const output = resolve(process.env.ETL_DESCRIPTION_UI_DIST || '.test-tmp/resource-description-build')
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('resource extension display, form, CSRF, conflict, permissions and file order', {
    skip: !existsSync(executable) || !existsSync(join(output, 'index.html')), timeout: 90000,
}, async () => {
    let editable = true, conflict = false, history = false, admin = false, historyError = false, historyEmpty = false, files = false
    let description = { resource_id: 'r1', description: 'Original description', authors: [{ name: 'Teacher', organization: 'School', role: null }],
        tags: ['school'], revision: 'a'.repeat(64), editable: true, unavailable_reason: null }
    const writes = [], exceptions = []
    const server = createServer(async (req, res) => {
        const path = new URL(req.url, 'http://localhost').pathname
        const json = (data, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data)) }
        if (path === '/health') return json({ status: 'ok' })
        if (path === '/api/v1/auth/config') return json({ mode: 'master_users', login_required: true, admin_feature_enabled: true })
        if (path === '/api/v1/auth/me') return json({ id: 'master:user-1', role: admin ? 'ADMIN' : 'VIEWER', roles: [admin ? 'admin' : 'viewer'], mode: 'master_users',
            display_name: 'Teacher', email: 'teacher@example.test', admin_feature_enabled: true, resource_ids: ['r1'] })
        if (path === '/api/v1/auth/csrf') return json({ csrf_token: 'isolated-resource-csrf' })
        if (path === '/api/v1/resources/r1') return json({ id: 'r1', title: 'Tin học 7 – Bảng tính thống kê lượt mượn thư viện', source_origin: 'POSTGRESQL',
            provider_name: 'School', resource_type_name_vi: 'PDF', publication_status: 'PUBLISHED', is_active: true,
            created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
            subjects: [{ id: 's1', name: 'Tin học', is_primary: true }],
            grade_levels: [{ id: 'g7', grade_name_vi: 'Lớp 7', grade_code: '7' }] })
        if (path === '/api/v1/resources/r1/versions') {
            if (historyError) return json({ detail: 'unavailable' }, 503)
            return json((history && !historyEmpty) || files ? [1, 3, 2].map(n => ({ id: `v${n}`, resource_id: 'r1',
                parent_version_id: n > 1 ? `v${n - 1}` : null, version_number: n, lifecycle_status: 'INGESTED', uploaded_by: null,
                created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z' })) : [])
        }
        if (/^\/api\/v1\/resource-versions\/v[123]\/index-status$/.test(path)) return json({ processing_status: 'CONTENT_READY', index_status: 'READY' })
        if (/^\/api\/v1\/resource-versions\/v[123]\/source-info$/.test(path)) return json({ ownership: 'SOURCE_BACKED', provider: 'google_drive',
            availability: 'AVAILABLE', can_open_original: true, original_url: 'https://drive.google.com/file/d/fixture/view' })
        if (/^\/api\/v1\/resource-versions\/v[123]\/file$/.test(path)) {
            res.writeHead(200, { 'Content-Type': 'text/plain' }); return res.end('Isolated file preview')
        }
        if (path === '/api/v1/resources/r1/description') {
            if (req.method === 'PATCH') {
                let raw = ''; for await (const part of req) raw += part
                const body = JSON.parse(raw); writes.push(body)
                assert.equal(req.headers['x-csrf-token'], 'isolated-resource-csrf')
                if (conflict) return json({ detail: { code: 'RESOURCE_DESCRIPTION_CHANGED' } }, 409)
                description = { ...description, ...body, revision: 'b'.repeat(64) }
            }
            return json({ ...description, editable })
        }
        if (path.startsWith('/api/')) return json({}, 404)
        const file = path.startsWith('/assets/') ? resolve(output, '.' + path) : join(output, 'index.html')
        if (!file.startsWith(output + sep)) { res.writeHead(403); return res.end() }
        try {
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png', '.svg': 'image/svg+xml' }[extname(file)] || 'application/octet-stream' })
            res.end(await readFile(file))
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const directory = await mkdtemp(join(tmpdir(), 'etl-resource-description-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', `--user-data-dir=${directory}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map()
    async function wait(fn) {
        for (let i = 0; i < 150; i++) { const value = await fn(); if (value) return value; await delay(100) }
        throw new Error('Resource UI assertion timed out')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout ' + method)) }, 15000)
            pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const value = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(value.exceptionDetails, undefined)
        return value.result.value
    }
    const click = label => evaluate(`Array.from(document.querySelectorAll('button')).find(e => e.textContent === ${JSON.stringify(label)}).click()`)
    const open = () => call('Page.navigate', { url: origin + '/library/r1' })
    async function capture(name) {
        const evidence = resolve('.test-tmp/resource-detail-profile-evidence')
        await mkdir(evidence, { recursive: true })
        const result = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
        await writeFile(join(evidence, `${name}.png`), Buffer.from(result.data, 'base64'))
    }
    try {
        const port = await wait(async () => { try { return Number((await readFile(join(directory, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null } })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const message = JSON.parse(event.data), item = pending.get(message.id)
            if (item) { pending.delete(message.id); if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result) }
            if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params)
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable'); await call('Runtime.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false })
        await open()
        await wait(() => evaluate("document.body?.textContent.includes('Original description')"))
        assert.equal(await evaluate("document.querySelector('#resource-description-title').textContent"), 'Thông tin chung')
        assert.equal(await evaluate("Array.from(document.querySelectorAll('h2')).some(e=>e.textContent==='Thông tin bổ sung')"), false)
        assert.equal(await evaluate("document.querySelector('[aria-labelledby=resource-description-title]').textContent.includes('Đơn vị cung cấp')"), true)
        assert.equal(await evaluate("document.querySelector('aside[aria-label=\"Tóm tắt học liệu\"]').getBoundingClientRect().right < document.querySelector('[aria-labelledby=resource-description-title]').getBoundingClientRect().left"), true)
        await capture('desktop')
        assert.equal(await evaluate("document.querySelector('[aria-labelledby=resource-description-title]').compareDocumentPosition(Array.from(document.querySelectorAll('h2')).find(e=>e.textContent.includes('Tệp')).closest('section')) & Node.DOCUMENT_POSITION_FOLLOWING"), 4)
        await click('Cập nhật thông tin'); await click('Hủy')
        assert.equal(writes.length, 0)
        await click('Cập nhật thông tin')
        await capture('editing')
        assert.equal(await evaluate("document.querySelector('[aria-labelledby=resource-description-title]').textContent.includes('Nguồn quản lý') && document.querySelector('[aria-labelledby=resource-description-title]').textContent.includes('School')"), true)
        assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('form label')).map(e=>e.firstChild.textContent)"),
            ['Mô tả', 'Thẻ học liệu (cách nhau bằng dấu phẩy)', 'Họ tên', 'Đơn vị', 'Vai trò'])
        await evaluate("const field=document.querySelector('textarea'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(field,'Updated description'); field.dispatchEvent(new Event('input',{bubbles:true}))")
        await evaluate("for(let i=0;i<4;i++) document.querySelector('form').requestSubmit()")
        await wait(() => evaluate("document.body.textContent.includes('Đã cập nhật thông tin học liệu.')"))
        assert.equal(writes.length, 1); assert.equal(writes[0].description, 'Updated description')
        assert.deepEqual(Object.keys(writes[0]).sort(), ['authors', 'description', 'revision', 'tags'])
        conflict = true; await click('Cập nhật thông tin'); await click('Lưu thay đổi')
        await wait(() => evaluate("document.body.textContent.includes('Thông tin đã thay đổi.')"))
        assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), true)
        conflict = false; await click('Tải lại thông tin')
        await wait(() => evaluate("!document.querySelector('form')"))
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
        assert.equal(await evaluate("document.querySelector('aside[aria-label=\"Tóm tắt học liệu\"]').getBoundingClientRect().bottom < document.querySelector('[aria-labelledby=resource-description-title]').getBoundingClientRect().top"), true)
        await capture('mobile')
        editable = false; await open()
        await wait(() => evaluate("document.body?.textContent.includes('Updated description')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).some(e=>e.textContent==='Cập nhật thông tin')"), false)
        files = true
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false })
        await open()
        await wait(() => evaluate("!!document.querySelector('a[title=\"Xem tệp trực tiếp tại nguồn trong tab mới\"]')"))
        assert.equal(await evaluate("document.querySelector('select[aria-label=\"Phiên bản tệp\"]').value"), 'v3')
        assert.equal(await evaluate("document.querySelector('a[download]').getAttribute('href')"), '/api/v1/resource-versions/v3/file?download=true')
        await evaluate("document.querySelector('#resource-file-title').scrollIntoView({block:'start'})")
        await capture('files-desktop')
        await click('Xem tệp')
        await wait(() => evaluate("document.body.textContent.includes('Isolated file preview')"))
        await click('Đóng tệp')
        assert.equal(await evaluate("!!document.querySelector('pre')"), false)
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
        await evaluate("document.querySelector('#resource-file-title').scrollIntoView({block:'start'})")
        await capture('files-mobile')
        files = false
        history = true
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false })
        const openHistory = () => call('Page.navigate', { url: origin + '/library/r1/versions' })
        await openHistory()
        await wait(() => evaluate("document.querySelectorAll('[aria-labelledby=version-history-title] ol > li').length === 3"))
        assert.deepEqual(await evaluate("Array.from(document.querySelectorAll('[aria-labelledby=version-history-title] ol > li strong')).map(e=>e.textContent)"),
            ['Phiên bản 3', 'Phiên bản 2', 'Phiên bản 1'])
        assert.equal(await evaluate("document.querySelector('[aria-labelledby=version-history-title] a').getAttribute('href')"), '/library/r1?versionId=v3')
        assert.equal(await evaluate("!!document.querySelector('a[href^=\"/library/indexing?\"]')"), false)
        await capture('history-desktop')
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
        await capture('history-mobile')
        admin = true; await openHistory()
        await wait(() => evaluate("!!document.querySelector('a[href^=\"/library/indexing?\"]')"))
        assert.equal(await evaluate("new URL(document.querySelector('a[href^=\"/library/indexing?\"]').href).searchParams.get('versionId')"), 'v3')
        historyError = true; await openHistory()
        await wait(() => evaluate("!!document.querySelector('[role=alert]')"))
        assert.equal(await evaluate("document.body.textContent.includes('Học liệu chưa có phiên bản nào.')"), false)
        historyError = false; historyEmpty = true; await openHistory()
        await wait(() => evaluate("document.body?.textContent.includes('Học liệu chưa có phiên bản nào.')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('a')).find(e=>e.textContent.includes('Quay lại danh sách học liệu')).getAttribute('href')"), '/library/list')
        historyError = false
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false })
        await call('Page.navigate', { url: origin + '/overview/summary' })
        await wait(() => evaluate("!!document.querySelector('aside[aria-label=\"Giới thiệu kho học liệu\"]')"))
        assert.equal(await evaluate("document.querySelector('h1').textContent"), 'Tổng quan hệ thống')
        assert.equal(await evaluate("!!document.querySelector('main a[href=\"/library/indexing\"]')"), true)
        assert.equal(await evaluate("document.querySelectorAll('.sub-menu a').length"), 2)
        await capture('overview-admin-desktop')
        admin = false
        await call('Page.navigate', { url: origin + '/overview/summary' })
        await wait(() => evaluate("!!document.querySelector('aside[aria-label=\"Giới thiệu kho học liệu\"]')"))
        assert.equal(await evaluate("!!document.querySelector('main a[href=\"/library/indexing\"]')"), false)
        assert.equal(await evaluate("!!document.querySelector('main a[href=\"/systems/status\"]')"), false)
        assert.equal(await evaluate("!!document.querySelector('main a[href=\"/search/query-methods\"]')"), true)
        await capture('overview-user-desktop')
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
        await capture('overview-mobile')
        await call('Page.navigate', { url: origin + '/overview/process' })
        await wait(() => evaluate("document.querySelector('h1')?.textContent==='Quy trình hoạt động'"))
        assert.equal(await evaluate("document.querySelectorAll('table tbody tr:not(:has(th[colspan]))').length"), 8)
        assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
        assert.equal(await evaluate("document.querySelector('[aria-label=\"Sơ đồ quy trình, có thể cuộn ngang\"]').scrollWidth > document.querySelector('[aria-label=\"Sơ đồ quy trình, có thể cuộn ngang\"]').clientWidth"), true)
        await capture('overview-process-mobile')
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1050, deviceScaleFactor: 1, mobile: false })
        await capture('overview-process-desktop')
        assert.deepEqual(exceptions, [])
    } finally {
        if (ws) ws.close()
        browser.kill()
        await new Promise(resolve => server.close(resolve))
    }
})

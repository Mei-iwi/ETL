// Built React pages exercised in Edge against isolated HTTP fixtures; no Dev data.
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
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('search form, methods, results, pagination, empty, error/retry and mobile layout', {
    skip: !existsSync(executable) || !existsSync(join(root, 'dist/index.html')), timeout: 60000,
}, async () => {
    let postCount = 0, contextCount = 0, mode = 'results', fileKind = 'image'
    let releaseSearch
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
            if (mode === 'slow') await new Promise(resolve => { releaseSearch = resolve })
            if (mode === 'error') return json({ detail: { code: 'BLOCKED_EMBEDDING_RUNTIME', message: 'Model chưa sẵn sàng.' } }, 503)
            if (mode === 'denied') return json({ detail: { code: 'SEARCH_ACCESS_RESTRICTED', message: 'Truy cập tìm kiếm nội bộ bị hạn chế.' } }, 403)
            if (mode === 'empty') return json({ ...body, items: [], total: 0, total_pages: 0, message: 'NO_READY_INDEX', ranking_algorithm: 'rrf-v1' })
            if (mode === 'public') return json({ ...body, access_scope: 'public', items: [], total: 0, total_pages: 0, message: 'PUBLIC_ACCESS_UNCONFIRMED', ranking_algorithm: 'rrf-v1' })
            return json({ ...body, access_scope: 'local_demo', total: 8, total_pages: 2, message: 'OK', ranking_algorithm: 'rrf-v1', items: Array.from({ length: body.page === 1 ? 5 : 3 }, (_, offset) => ({
                resource_id: `r${(body.page - 1) * 5 + offset + 1}`, resource_title: `Học liệu ${(body.page - 1) * 5 + offset + 1}`, resource_version_id: 'v1', version_number: 1,
                provider_name: 'Nhà cung cấp', resource_type_code: 'DOC', bag_id: 'b1', representation_mode: 'fallback',
                score: .03, score_components: { bow: .5, vector: .7 }, matched_bag_count: 1, matched_keywords: ['toán'],
                snippet: 'Văn bản <script>không được thực thi</script>', page_from: 2, page_to: 2, content_unit_id: 'unit-1', context_id: 'token-1',
            })) })
        }
        if (url.pathname === '/api/v1/chunks/token-1/context') {
            contextCount++
            return json({ resource_id: 'r1', resource_version_id: 'v1', content_unit_id: 'unit-1',
                heading_path: [{ title: 'Chương kiểm thử', unit_type: 'CHAPTER' }],
                chunks: [{ id: 'c1', content_unit_id: 'unit-1', unit_chunk_sequence: 1, char_start: 0,
                    char_end: 20, page_from: 2, page_to: 2, selected: true, text: 'Nội dung ngữ cảnh được chọn' }] })
        }
        if (url.pathname === '/api/v1/resources/r1') return json({ id: 'r1', title: 'Học liệu 1', provider_name: 'Nhà cung cấp',
            resource_type_name_vi: 'Tài liệu', publication_status: 'PUBLISHED', is_active: true, created_at: '2026-10-16', updated_at: '2026-10-16', subjects: [], grade_levels: [] })
        if (url.pathname === '/api/v1/resources/r1/versions') return json([{ id: 'v1', version_number: 1, created_at: '2026-10-16' }])
        if (url.pathname === '/api/v1/resource-versions/v1/index-status') return json({ processing_status: 'CONTENT_READY', index_status: 'READY' })
        if (url.pathname === '/api/v1/resource-versions/v1/file') {
            if (url.searchParams.get('view') === 'text') {
                res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' })
                return res.end('Nội dung Word thử nghiệm')
            }
            if (fileKind === 'office') {
                res.writeHead(200, { 'Content-Type': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
                return res.end('isolated-office-fixture')
            }
            res.writeHead(200, { 'Content-Type': 'image/png' })
            return res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jD1sAAAAASUVORK5CYII=', 'base64'))
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
        await open('/search')
        await waitFor(() => evaluate("document.querySelector('fieldset button[aria-pressed=\"true\"]')?.textContent === 'Tìm kết hợp'"))
        await capture('search-welcome-desktop')
        assert.equal(await evaluate("document.querySelectorAll('nav[aria-label=\"Menu Tra cứu\"] a').length"), 1)
        await click('Phương trình bậc hai')
        assert.equal(await evaluate("document.querySelector('#search-query').value"), 'Phương trình bậc hai')
        assert.equal(await evaluate("document.activeElement.id"), 'search-query')
        await click('Theo ý nghĩa')
        assert.equal(await evaluate("document.querySelector('#search-method-help').textContent.includes('cách diễn đạt khác')"), true)
        await click('Theo từ khóa')
        await waitFor(() => evaluate("document.querySelector('fieldset button[aria-pressed=\"true\"]')?.textContent === 'Theo từ khóa'"))
        assert.equal(postCount, 0)
        await waitFor(() => evaluate("document.querySelector('#search-grade')?.disabled === false"))
        await evaluate("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(document.querySelector('#search-query'), 'toán'); document.querySelector('#search-query').dispatchEvent(new Event('input', { bubbles: true }))")
        await evaluate("document.querySelector('form details').open=true")
        await evaluate("document.querySelector('#search-grade').value='g1'; document.querySelector('#search-grade').dispatchEvent(new Event('change', { bubbles: true }))")
        await evaluate("document.querySelector('#search-subject').value='s1'; document.querySelector('#search-subject').dispatchEvent(new Event('change', { bubbles: true }))")
        await evaluate("document.querySelector('#search-type').value='DOC'; document.querySelector('#search-type').dispatchEvent(new Event('change', { bubbles: true }))")
        assert.equal(await evaluate("document.querySelectorAll('button[aria-label^=\"Bỏ bộ lọc:\"]').length"), 3)
        await capture('search-filters-desktop')
        await evaluate("document.querySelector('button[aria-label=\"Bỏ bộ lọc: Lớp 6\"]').click()")
        assert.equal(await evaluate("document.querySelector('#search-subject').value"), '')
        assert.equal(await evaluate("document.querySelectorAll('button[aria-label^=\"Bỏ bộ lọc:\"]').length"), 1)
        await evaluate("Array.from(document.querySelectorAll('button')).find(button => button.textContent.trim() === 'Xóa bộ lọc').click()")
        assert.equal(await evaluate("document.querySelectorAll('button[aria-label^=\"Bỏ bộ lọc:\"]').length"), 0)
        await evaluate("document.querySelector('#search-grade').value='g1'; document.querySelector('#search-grade').dispatchEvent(new Event('change', { bubbles: true }))")
        await evaluate("document.querySelector('#search-query').focus()")
        await call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13,
            nativeVirtualKeyCode: 13, text: '\r', unmodifiedText: '\r' })
        await call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        assert.equal(postCount, 1, 'only one POST on submission, including React StrictMode')
        assert.equal(bodies[0].method, 'bow')
        assert.equal(bodies[0].size, 5)
        assert.equal(await evaluate("document.querySelectorAll('article').length"), 5)
        assert.equal(await evaluate("document.querySelector('nav[aria-label=\"Menu Tra cứu\"] a').getAttribute('aria-current')"), 'page')
        assert.equal(await evaluate("document.querySelector('article').textContent.includes('Tương đồng Vector')"), true)
        assert.equal(bodies[0].filters.grade_level_id, 'g1')
        assert.equal(await evaluate("document.querySelector('article script')"), null)
        assert.equal(contextCount, 0, 'context is lazy, never fetched for every result on mount')
        assert.equal(await evaluate("document.querySelector('article').textContent.includes('Văn bản')"), true)
        assert.equal(await evaluate("document.querySelector('article details').open"), false)
        await evaluate("document.querySelector('article details summary').click()")
        assert.equal(await evaluate("document.querySelector('article details').open"), true)
        assert.ok(await evaluate("document.querySelector('article dl').getBoundingClientRect().height > 0"))
        await evaluate("document.querySelector('article details summary').click()")
        assert.equal(await evaluate("!!document.querySelector('article button')"), false)
        async function assertFixedSearch() {
            const top = await evaluate("document.querySelector('form').getBoundingClientRect().top")
            await evaluate("document.querySelector('section[aria-label=\"Kết quả tìm kiếm\"]').scrollTop = 10000")
            assert.ok(await evaluate("document.querySelector('section[aria-label=\"Kết quả tìm kiếm\"]').scrollTop > 0"))
            assert.equal(await evaluate("document.querySelector('form').getBoundingClientRect().top"), top)
            assert.equal(await evaluate("document.querySelector('main').scrollTop"), 0)
            assert.equal(await evaluate("getComputedStyle(document.querySelector('article')).borderTopWidth"), '1px')
            await evaluate("document.querySelector('section[aria-label=\"Kết quả tìm kiếm\"]').scrollTop = 0")
        }
        await assertFixedSearch()
        async function capture(name) {
            if (!process.env.ETL_TEST_CAPTURE_LAYOUT) return
            const directory = resolve(root, '../.test-tmp/search-layout')
            await mkdir(directory, { recursive: true })
            const result = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
            await writeFile(join(directory, name + '.png'), Buffer.from(result.data, 'base64'))
        }
        await capture('search-context-desktop')
        assert.equal(contextCount, 0)
        assert.equal(await evaluate("document.querySelector('section[aria-label=\"Ngữ cảnh học liệu\"]')"), null)
        await click('Tiếp')
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 6')"))
        assert.equal(bodies.at(-1).page, 2)
        await evaluate('history.back()')
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        assert.equal(await evaluate("!!document.querySelector('#search-query')"), true)
        await waitFor(() => evaluate("document.querySelector('#search-query')?.value === 'toán'"))
        await waitFor(() => evaluate("document.querySelector('#search-grade')?.value === 'g1'"))
        await evaluate("document.querySelector('article h2 a').click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Tệp học liệu')"))
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Xem tệp')?.disabled === false"))
        await click('Xem tệp')
        await waitFor(() => evaluate("document.querySelector('img[alt=\"Nội dung tệp học liệu\"]')?.naturalWidth > 0"))
        assert.ok(await evaluate("document.querySelector('a[download]').href.endsWith('/api/v1/resource-versions/v1/file?download=true')"))
        await click('Đóng tệp')
        fileKind = 'office'
        await click('Xem tệp')
        await waitFor(() => evaluate("document.querySelector('pre')?.textContent.includes('Nội dung Word thử nghiệm')"))
        await click('← Quay lại kết quả tìm kiếm')
        await waitFor(() => evaluate("!!document.querySelector('article')"))
        mode = 'error'
        await open('/search/result?q=toán&method=vector')
        await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('Model chưa sẵn sàng')"))
        assert.equal(await evaluate("getComputedStyle(document.querySelector('[role=alert]')).color"), 'rgb(185, 28, 28)')
        mode = 'empty'
        await click('Thử lại')
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa có học liệu lập chỉ mục sẵn sàng')"))
        assert.equal(await evaluate("!!document.querySelector('a[href=\"/library/indexing\"]')"), false)
        mode = 'public'
        await open('/search/result?q=toán&method=bow')
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa có học liệu đủ quyền truy xuất công khai')"))
        assert.equal(await evaluate("document.body.textContent.includes('Tra cứu thử nghiệm nội bộ')"), false)
        mode = 'denied'
        await open('/search/result?q=toán&method=hybrid')
        await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('Truy cập tìm kiếm nội bộ bị hạn chế')"))
        mode = 'results'
        await open('/search/result?q=toán&method=hybrid')
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        assert.equal(await evaluate("document.body.textContent.includes('Tra cứu thử nghiệm nội bộ')"), true)
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate("document.querySelector('article').getBoundingClientRect().width <= innerWidth"), true)
        assert.ok(await evaluate("document.querySelector('#search-query').getBoundingClientRect().width >= 140"))
        assert.equal(await evaluate("document.querySelector('form').scrollWidth <= document.querySelector('form').clientWidth"), true)
        await assertFixedSearch()
        assert.equal(contextCount, 0)
        await capture('search-context-mobile')
        // Hold a real HTTP response: no invented model phase or repeated POST.
        mode = 'slow'
        const beforeSlow = postCount
        await open('/search/result?q=slow&method=vector')
        await waitFor(() => postCount === beforeSlow + 1 && !!releaseSearch)
        assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), true)
        assert.equal(await evaluate("document.querySelector('section[aria-busy=true]')?.textContent.includes('Đang tìm kiếm')"), true)
        assert.equal(await evaluate("document.body.textContent.includes('Đang khởi tạo mô hình')"), false)
        await evaluate("for(let i=0;i<10;i++) document.querySelector('form').requestSubmit()")
        await delay(200)
        assert.equal(postCount, beforeSlow + 1, 'rapid keyboard/submission stays single flight')
        // Navigate while the old server request is still computing. Its late
        // response must not replace the new route or report server cancellation.
        mode = 'empty'
        await evaluate("history.pushState({}, '', '/search/result?q=new&method=bow'); window.dispatchEvent(new PopStateEvent('popstate'))")
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa có học liệu lập chỉ mục sẵn sàng')"))
        releaseSearch()
        await delay(250)
        assert.equal(await evaluate("document.querySelectorAll('article').length"), 0)
        assert.equal(await evaluate("document.querySelector('#search-query').value"), 'new')
        assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), false)

    } finally {
        releaseSearch?.()
        if (ws) ws.close()
        browser.kill()
        await new Promise(resolve => server.close(resolve))
        await delay(400)
        await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
    }
})

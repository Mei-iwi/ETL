// Built React pages exercised in Edge against isolated HTTP fixtures; no Dev data.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, extname, join, resolve, sep } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = process.env.ETL_T4_FRONTEND_ROOT || resolve(dirname(fileURLToPath(import.meta.url)), '..')
const baseline = process.env.ETL_T4_BASELINE === '1'
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('search form, methods, results, pagination, empty, error/retry and mobile layout', {
    skip: !existsSync(executable) || !existsSync(join(root, 'dist/index.html')), timeout: 90000,
}, async () => {
    let postCount = 0, contextCount = 0, catalogCount = 0, mode = 'results', fileKind = 'image'
    let resultCount = 8
    let modelState = 'LOADING'
    let authRole = null
    let canViewFile = true
    let contextEvidence = 'lexical'
    let metadataFields = ['TITLE']
    let holdAuthConfig = true
    const reconciliationBodies = []
    let reconciliationPolls = 0
    let releaseSearch
    const bodies = [], responses = []
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost')
        const json = (body, status = 200) => {
            const wire = JSON.stringify(body)
            if (url.pathname === '/api/v1/search') responses.push({ status, sha256: createHash('sha256').update(wire).digest('hex') })
            res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(wire)
        }
        if (url.pathname === '/health') return json({ status: 'ok', database: 'ok' })
        if (url.pathname === '/api/v1/auth/config') {
            if (holdAuthConfig) return // Simulate the backend accepting TCP but never responding.
            return json({ mode: authRole ? 'master_users' : 'legacy_local', login_required: !!authRole })
        }
        if (url.pathname === '/api/v1/auth/me') return json({ id: 'master:search-reader', mode: 'master_users',
            role: authRole, roles: [authRole === 'ADMIN' ? 'admin' : 'viewer'], resource_ids: [], admin_feature_enabled: true })
        if (url.pathname === '/api/v1/auth/csrf') return json({ csrf_token: 'search-fixture-csrf' })
        if (url.pathname === '/api/v1/search/model-status') return json({ state: modelState, ready: modelState === 'READY' })
        if (url.pathname === '/api/v1/search/model-retry') {
            assert.equal(req.method, 'POST')
            return json({ state: modelState, ready: modelState === 'READY' })
        }
        if (['/api/v1/subjects', '/api/v1/grade-levels', '/api/v1/resource-types'].includes(url.pathname)) {
            catalogCount++
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
            body.representation_unit = 'content_bag'
            body.ready_bag_count = 1
            if (mode === 'slow') await new Promise(resolve => { releaseSearch = resolve })
            if (mode === 'malformed') { res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end('{broken') }
            if (mode === 'null') return json(null)
            if (mode === 'array') return json([])
            if (mode === 'missing') return json({ total: 1, items: [] })
            if (mode === 'network') return res.destroy()
            if (mode === 'error') return json({ detail: { code: 'BLOCKED_EMBEDDING_RUNTIME', message: 'Model chưa sẵn sàng.' } }, 503)
            if (mode === 'denied') return json({ detail: { code: 'SEARCH_ACCESS_RESTRICTED', message: 'Truy cập tìm kiếm nội bộ bị hạn chế.' } }, 403)
            if (mode === 'revoked') return json({ detail: { code: 'SEARCH_UNAVAILABLE' } }, 401)
            if (mode === 'stale') return json({ detail: { code: 'SEARCH_INDEX_STALE' } }, 409)
            if (mode === 'busy') return json({ detail: { code: 'SEARCH_RUNTIME_BUSY' } }, 503)
            if (mode === 'empty') return json({ ...body, items: [], total: 0, total_pages: 0, message: 'NO_READY_INDEX', ranking_algorithm: 'rrf-v1' })
            if (mode === 'public') return json({ ...body, access_scope: 'public', items: [], total: 0, total_pages: 0, message: 'PUBLIC_ACCESS_UNCONFIRMED', ranking_algorithm: 'rrf-v1' })
            return json({ ...body, access_scope: 'local_demo', total: resultCount, total_pages: Math.ceil(resultCount / body.size), message: 'OK', ranking_algorithm: 'rrf-v1', items: Array.from({ length: Math.max(0, Math.min(body.size, resultCount - (body.page - 1) * body.size)) }, (_, offset) => ({
                resource_id: `r${(body.page - 1) * 5 + offset + 1}`, resource_title: `Học liệu ${(body.page - 1) * 5 + offset + 1}`, resource_version_id: 'v1', version_number: 1,
                can_view_file: canViewFile,
                provider_name: 'Nhà cung cấp', resource_type_code: 'DOC', bag_id: 'b1', bag_title: null, unit_type: 'SECTION', representation_mode: 'fallback',
                score: .03, score_components: { bow: .5, vector: .7 }, matched_bag_count: 1, matched_keywords: ['toán'],
                snippet: 'Văn bản <script>không được thực thi</script>' + (contextEvidence === 'semantic' ? ' Machine learning' : ''), page_from: 2, page_to: 2, content_unit_id: 'unit-1', context_id: 'token-1', context_url: '/api/v1/chunks/token-1/context',
                context_evidence: contextEvidence,
                lexical_evidence: contextEvidence === 'semantic' ? [{ query_phrase: 'học máy', matched_phrase: 'Machine learning',
                    concept_id: 'etl:machine_learning', relation: 'translation', field: 'content', weight: 0.65,
                    sources: [{ name: 'curated', identifier: 'fixture' }] }] : [],
                meaning_variants: contextEvidence === 'semantic' ? ['học máy', 'machine learning', 'hệ thống học từ dữ liệu'] : [],
                ...(mode === 'metadata' ? { match_origin: 'METADATA', matched_fields: metadataFields,
                    evidence_kind: 'METADATA_FIELD', evidence_text: 'Thông tin học liệu đã đồng bộ', resource_format: 'mp4',
                    representation_mode: 'metadata', bag_id: null, content_unit_id: null, context_id: null,
                    context_url: null, can_view_file: false, matched_bag_count: 0, matched_keywords: [],
                    page_from: null, page_to: null, snippet: 'Thông tin học liệu đã đồng bộ' } : {}),
                ...(mode === 'both' ? { match_origin: 'BOTH', matched_fields: ['CONTENT', 'TITLE'], resource_format: 'pdf' } : {}),
            })) })
        }
        if (url.pathname === '/api/v1/chunks/token-1/context') {
            contextCount++
            return json({ resource_id: 'r1', resource_title: 'Học liệu 1', resource_version_id: 'v1', content_unit_id: 'unit-1',
                bag_id: 'b1', version_number: 1, context_id: 'token-1', neighbors: 1,
                heading_path: [{ content_unit_id: 'unit-1', title: 'Chương kiểm thử', unit_type: 'CHAPTER' }],
                chunks: [{ id: 'c1', content_unit_id: 'unit-1', unit_chunk_sequence: 1, char_start: 0,
                    char_end: 20, page_from: 2, page_to: 2, selected: true, text: 'Nội dung ngữ cảnh được chọn' }] })
        }
        if (url.pathname === '/api/v1/resources/r1') return json({ id: 'r1', title: 'Học liệu 1', provider_name: 'Nhà cung cấp',
            resource_type_name_vi: 'Tài liệu', publication_status: 'PUBLISHED', is_active: true, created_at: '2026-10-16', updated_at: '2026-10-16', subjects: [], grade_levels: [] })
        if (url.pathname === '/api/v1/resources/r1/versions') return json([{ id: 'v1', version_number: 1, created_at: '2026-10-16' }])
        if (url.pathname === '/api/v1/resource-versions/v1/index-status') return json({ processing_status: 'CONTENT_READY', index_status: 'READY' })
        if (url.pathname === '/api/v1/resource-versions/v1/file') {
            if (fileKind === 'office' && url.searchParams.get('view') === 'preview') {
                res.writeHead(200, { 'Content-Type': 'application/pdf' })
                return res.end('%PDF-1.4\n% Isolated preview response; conversion is tested in backend tests.\n%%EOF')
            }
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
        if (url.pathname === '/api/v1/resources') return json({ items: [], pagination: { page: 1, size: 7, total: 0, total_pages: 0 } })
        if (url.pathname === '/api/v1/reconciliation/runs/latest' && reconciliationBodies.length && ++reconciliationPolls < 3) return json({
            latest_run_id: 'fixture-audit', overall_status: 'RUNNING', overall_match_rate_percent: 0,
            total_source_records: 0, total_target_records: 1, total_matched_records: 0, total_mismatched_records: 0,
            total_source_bytes: 0, total_target_bytes: 0,
            stream_reports: [{ run_id: 'fixture-audit', stream_name: 'files', status: 'RUNNING',
                summary_meta: { processed_files: 0, total_files: 1 } }] })
        if (url.pathname === '/api/v1/reconciliation/runs/latest' && reconciliationBodies.length) return json({
            latest_run_id: 'fixture-audit', overall_status: 'PARTIAL', overall_match_rate_percent: 0,
            total_source_records: 1, total_target_records: 1, total_matched_records: 0, total_mismatched_records: 0,
            total_source_bytes: 0, total_target_bytes: 0,
            stream_reports: [{ run_id: 'fixture-audit', stream_name: 'files', status: 'PARTIAL',
                summary_meta: { unverified_source_files: 1 } }] })
        if (url.pathname === '/api/v1/reconciliation/discrepancies') return json({
            items: [{ id: 'audit-item', run_id: 'fixture-audit', record_id: 'version-1', stream_name: 'files',
                discrepancy_flag: 'NOT_VERIFIED', source_value: { source_file_size: 100 },
                target_value: { reason_code: 'SOURCE_REFERENCE_DISABLED' }, resolution_status: 'UNRESOLVED' }],
            total: 1, page: 1, page_size: 10, total_pages: 1 })
        if (url.pathname === '/api/v1/reconciliation/runs/latest') return json({
            overall_status: 'NO_RUNS_YET', overall_match_rate_percent: 0, total_source_records: 0,
            total_target_records: 0, total_matched_records: 0, total_mismatched_records: 0,
            total_source_bytes: 0, total_target_bytes: 0, stream_reports: [] })
        if (url.pathname === '/api/v1/reconciliation/runs') {
            let body = ''; for await (const chunk of req) body += chunk
            reconciliationBodies.push(JSON.parse(body))
            reconciliationPolls = 0
            return json({ status: 'RUNNING', reports: [{ run_id: 'fixture-audit', stream_name: 'files', status: 'RUNNING' }] }, 202)
        }
        if (url.pathname.startsWith('/api/')) return json({ detail: 'not found' }, 404)
        const file = url.pathname.startsWith('/assets/') ? resolve(root, 'dist', '.' + url.pathname) : join(root, 'dist/index.html')
        if (!file.startsWith(join(root, 'dist') + sep)) { res.writeHead(403); return res.end() }
        try {
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png', '.jpg': 'image/jpeg' }[extname(file)] || 'application/octet-stream' })
            res.end(await readFile(file))
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const profile = await mkdtemp(join(tmpdir(), 'etl-search-ui-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        // Synthetic benchmark intentionally exceeds normal human history rates.
        ...(process.env.ETL_T4_METRICS ? ['--disable-ipc-flooding-protection'] : []),
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
        await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('Dịch vụ xác thực chưa khả dụng')"))
        assert.equal(await evaluate("document.body.textContent.includes('Đang kiểm tra phiên đăng nhập')"), false)
        holdAuthConfig = false
        await click('Thử lại')
        await waitFor(() => evaluate("document.querySelector('fieldset button[aria-pressed=\"true\"]')?.textContent === 'Tìm kết hợp'"))
        await waitFor(() => evaluate("document.body.textContent.includes('Đang chuẩn bị tìm kiếm')"))
        assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), true)
        assert.equal(await evaluate("Array.from(document.querySelectorAll('fieldset button')).find(b => b.textContent === 'Theo từ khóa').disabled"), false)
        assert.equal(postCount, 0)
        modelState = 'FAILED'
        await click('Kiểm tra lại')
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa thể chuẩn bị tìm kiếm')"))
        assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), true)
        modelState = 'READY'
        await click('Thử lại')
        await waitFor(() => evaluate("document.querySelector('button[type=submit]').disabled === false"))
        await capture('search-welcome-desktop')
        assert.equal(await evaluate("document.querySelectorAll('nav[aria-label=\"Menu Tra cứu\"] a').length"), 1)
        await click('Phương trình bậc hai')
        assert.equal(await evaluate("document.querySelector('#search-query').value"), 'Phương trình bậc hai')
        assert.equal(await evaluate("document.activeElement.id"), 'search-query')
        await click('Theo ý nghĩa')
        assert.equal(await evaluate("document.querySelector('fieldset button[aria-pressed=\"true\"]')?.textContent"), 'Theo ý nghĩa')
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
        assert.equal(await evaluate("document.querySelector('article button')?.textContent"), 'Ngữ cảnh')
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
            await delay(450)
            const directory = resolve(process.env.ETL_TEST_LAYOUT_OUTPUT || resolve(root, '../.test-tmp/search-layout'))
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
        mode = 'empty'
        const beforeVisibilityChange = postCount
        await evaluate("window.dispatchEvent(new Event('focus'))")
        await waitFor(() => postCount > beforeVisibilityChange)
        await waitFor(() => evaluate("document.querySelectorAll('article').length === 0 && document.body.textContent.includes('Chưa tìm thấy học liệu')"))
        mode = 'results'
        await click('Cập nhật kết quả')
        await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        // Explicit refresh must validate with the server and discard cached
        // snapshots on access/revision failures, rather than resurrect old text.
        for (const failure of ['revoked', 'denied', 'stale', 'busy']) {
            mode = failure
            const before = postCount
            await evaluate("window.t4Form = document.querySelector('form'); undefined")
            await click('Cập nhật kết quả')
            await waitFor(() => evaluate("!!document.querySelector('[role=alert]')"))
            assert.equal(postCount, before + 1)
            assert.equal(await evaluate("document.querySelectorAll('article').length"), 0)
            if (!baseline) assert.equal(await evaluate("window.t4Form === document.querySelector('form')"), true)
            mode = 'results'
            await click('Thử lại')
            await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        }
        if (process.env.ETL_T4_METRICS) {
            resultCount = 13
            const samples = []
            await call('Performance.enable')
            async function measured(temperature, group, action, expected) {
                const posts = postCount, catalogs = catalogCount
                const cpu = process.cpuUsage()
                const start = performance.now()
                await action()
                const result = await call('Runtime.evaluate', { awaitPromise: true, returnByValue: true,
                    expression: `new Promise((resolve, reject) => {
                        const ready = () => document.querySelector('article h2')?.textContent.trim() === ${JSON.stringify(expected)} && document.querySelector('section[aria-label="Kết quả tìm kiếm"]')?.textContent.includes(new URLSearchParams(location.search).get('q'));
                        if (ready()) return resolve(true);
                        const timer = setTimeout(() => { observer.disconnect(); reject(new Error('page timeout')) }, 5000);
                        const observer = new MutationObserver(() => { if (ready()) { clearTimeout(timer); observer.disconnect(); resolve(true) } });
                        observer.observe(document.body, { childList: true, subtree: true });
                    })` }).catch(async error => {
                        const state = await evaluate("({url: location.pathname + location.search, heading: document.querySelector('article h2')?.textContent, alert: document.querySelector('[role=alert]')?.textContent})")
                        throw new Error(`${temperature}/${group}: ${error.message}; fixture UI state=${JSON.stringify(state)}`)
                    })
                assert.equal(result.exceptionDetails, undefined, `${temperature}/${group}`)
                assert.equal(postCount - posts, ['visited', 'back'].includes(group) ? 0 : 1,
                    'visited pages reuse recent tab snapshots; new pages use live Search')
                const usage = process.cpuUsage(cpu)
                const memory = process.memoryUsage()
                assert.equal(responses.at(-1).status, 200)
                samples.push({ temperature, group, wall_ms: performance.now() - start,
                    posts: postCount - posts, catalogs: catalogCount - catalogs,
                    harness_cpu_ms: (usage.user + usage.system) / 1000, harness_rss_bytes: memory.rss,
                    cache: 'OFF', SQL: null, Mongo: null, encodes: null, reranks: null,
                    response: responses.at(-1) })
            }
            for (const temperature of ['cold', 'warm']) for (let i=0;i<30;i++) {
                const path = `/search/query-methods?q=bench${temperature}${i}&method=bow`
                const navigate = () => evaluate(`history.pushState({}, '', ${JSON.stringify(path)}); window.dispatchEvent(new PopStateEvent('popstate'))`)
                // Cold means a new SPA Search form after leaving Search, not a
                // cold browser/model. Warm means the app is already on Search.
                if (temperature === 'cold') {
                    await evaluate("history.pushState({}, '', '/search'); window.dispatchEvent(new PopStateEvent('popstate'))")
                    await waitFor(() => evaluate("!document.querySelector('article') && document.querySelector('#search-query')?.value === ''"))
                }
                await measured(temperature, 'first', navigate, 'Học liệu 1')
                await measured(temperature, 'new-2', () => click('Tiếp'), 'Học liệu 6')
                await measured(temperature, 'new-3', () => click('Tiếp'), 'Học liệu 11')
                await measured(temperature, 'visited', () => evaluate('history.back()'), 'Học liệu 6')
                await measured(temperature, 'back', () => evaluate('history.back()'), 'Học liệu 1')
            }
            await writeFile(process.env.ETL_T4_METRICS, JSON.stringify({ baseline,
                fixture: 'Edge isolated HTTP; cold=form lifetime, warm=existing Search; CPU/RSS=Node harness only; SQL/Mongo/model not present (null)',
                samples }, null, 2))
            resultCount = 8
            await open('/search/query-methods?q=toán&method=bow')
            await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Học liệu 1')"))
        }
        await evaluate("document.querySelector('article h2 a').click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Tệp học liệu')"))
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Xem tệp')?.disabled === false"))
        await click('Xem tệp')
        await waitFor(() => evaluate("document.querySelector('img[alt=\"Nội dung tệp học liệu\"]')?.naturalWidth > 0"))
        assert.ok(await evaluate("document.querySelector('a[download]').href.endsWith('/api/v1/resource-versions/v1/file?download=true')"))
        await click('Đóng tệp')
        fileKind = 'office'
        await click('Xem tệp')
        await waitFor(() => evaluate("document.querySelector('iframe[title=\"Xem tệp PDF\"]')?.src.startsWith('blob:')"))
        assert.equal(await evaluate("document.querySelector('pre')"), null)
        assert.ok(await evaluate("document.querySelector('a[download]').href.endsWith('/api/v1/resource-versions/v1/file?download=true')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('a')).find(link => link.textContent.includes('Quay lại danh sách học liệu'))?.getAttribute('href')"), '/library/list')
        await evaluate('history.back()')
        await waitFor(() => evaluate("!!document.querySelector('article')"))
        mode = 'metadata'; resultCount = 1
        for (const [fields, label] of [[['TITLE'], 'Khớp tiêu đề'], [['DESCRIPTION'], 'Khớp mô tả'],
            [['TAGS'], 'Khớp thẻ/từ khóa'], [[], 'Khớp thông tin học liệu']]) {
            metadataFields = fields
            const contextsBefore = contextCount
            await open('/search/result?q=metadata&method=vector')
            await waitFor(() => evaluate(`document.body.textContent.includes(${JSON.stringify(label)})`))
            assert.equal(await evaluate("document.querySelectorAll('article').length"), 1)
            assert.equal(await evaluate("document.querySelector('article a')?.getAttribute('href').includes('versionId=v1')"), true)
            assert.equal(contextCount, contextsBefore)
            assert.equal(await evaluate("document.body.textContent.includes('Kh?p')"), false)
        }
        mode = 'both'
        await open('/search/result?q=both&method=hybrid')
        await waitFor(() => evaluate("document.body.textContent.includes('Khớp nội dung và thông tin học liệu')"))
        assert.equal(await evaluate("document.querySelectorAll('article').length"), 1)
        resultCount = 8
        mode = 'error'
        await open('/search/result?q=toán&method=vector')
        await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('Chưa thể chuẩn bị tìm kiếm')"))
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
        assert.equal(await evaluate("document.body.textContent.includes('Đang chuẩn bị tìm kiếm')"), false)
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

        // Completed malformed/network responses must leave loading and permit retry.
        for (const failure of ['malformed', 'null', 'array', 'missing', 'network']) {
            mode = failure
            await open(`/search/result?q=${failure}&method=bow`)
            await waitFor(() => evaluate("!!document.querySelector('[role=alert]')"))
            assert.equal(await evaluate("!!document.querySelector('section[aria-busy=true]')"), false)
            assert.equal(await evaluate("document.querySelector('button[type=submit]').disabled"), false)
            assert.equal(await evaluate("document.body.textContent.includes('Đang tìm kiếm')"), false)
            mode = 'results'
            await click('Thử lại')
            await waitFor(() => evaluate("document.querySelectorAll('article').length === 5"))
        }

        authRole = 'VIEWER'
        await open('/search/query-methods?q=public&method=bow')
        await waitFor(() => evaluate("document.querySelectorAll('article').length === 5"))
        assert.equal(await evaluate("!!document.querySelector('article h2 a')"), true,
            'Authenticated viewers can open whole-file eligible public results')
        await evaluate("document.querySelector('article h2 a').click()")
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Xem tệp')?.disabled === false"))
        await click('Xem tệp')
        await waitFor(() => evaluate("!!document.querySelector('iframe[title=\"Xem tệp PDF\"]')"))
        assert.equal(await evaluate("!!document.querySelector('a[download]')"), true)
        await open('/search/query-methods?q=public&method=bow')
        await waitFor(() => evaluate("document.querySelectorAll('article').length === 5"))
        await evaluate("document.querySelector('article button').click()")
        await waitFor(() => evaluate("document.querySelector('article').textContent.includes('Nội dung ngữ cảnh được chọn')"))
        assert.equal(contextCount, 1)
        canViewFile = false
        await open('/search/query-methods?q=partial&method=bow')
        await waitFor(() => evaluate("document.querySelectorAll('article').length === 5"))
        assert.equal(await evaluate("!!document.querySelector('article h2 a')"), false,
            'A public snippet must not imply permission to read restricted parts of the whole file')
        canViewFile = true
        authRole = 'ADMIN'
        await open('/search/query-methods?q=public&method=bow')
        await waitFor(() => evaluate("!!document.querySelector('article h2 a')"))
        // Follow the actual result locator and verify both passage evidence states.
        for (const evidence of ['lexical', 'semantic', 'bag']) {
            contextEvidence = evidence
            mode = 'results'
            await open('/search/result?q=toán&method=bow')
            await waitFor(() => evaluate("!!document.querySelector('article h2 a')"))
            assert.equal(await evaluate("new URL(document.querySelector('article h2 a').href).searchParams.get('contextEvidence')"), evidence)
            if (evidence === 'semantic') {
                await evaluate("Array.from(document.querySelectorAll('summary')).find(item => item.textContent === 'Thuật ngữ tương đương trong học liệu').click()")
                assert.ok(await evaluate("Array.from(document.querySelectorAll('details[open] li')).some(item => item.textContent.includes('học máy') && item.textContent.includes('Machine learning'))"))
                await evaluate("Array.from(document.querySelectorAll('summary')).find(item => item.textContent === 'Các cách diễn đạt trong nhóm nghĩa khớp').click()")
                assert.ok(await evaluate("Array.from(document.querySelectorAll('details[open] li')).some(item => item.textContent === 'hệ thống học từ dữ liệu')"))
            }
            const beforeContext = contextCount
            await evaluate("document.querySelector('article h2 a').click()")
            await waitFor(() => evaluate("document.body.textContent.includes('Tệp học liệu')"))
            assert.equal(contextCount, beforeContext, 'passage loading remains lazy')
            await click('Ngữ cảnh')
            await waitFor(() => evaluate("document.querySelector('section[aria-label=\"Ngữ cảnh học liệu\"]')?.textContent.includes('Nội dung ngữ cảnh được chọn')"))
            const content = await evaluate("document.querySelector('section[aria-label=\"Ngữ cảnh học liệu\"]').textContent")
            assert.ok(content.includes(evidence === 'lexical' ? 'Đoạn có từ/cụm từ khớp truy vấn' : evidence === 'semantic' ? 'Đoạn gần nghĩa với truy vấn' : 'chưa xác định được đoạn khớp trực tiếp'))
            assert.equal(contextCount, beforeContext + 1)
        }

        // Horizontal overview and reconciliation dialogs use the real built UI.
        authRole = 'ADMIN'
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false })
        await call('Page.navigate', { url: origin + '/overview/summary' })
        await waitFor(() => evaluate("document.querySelector('img[alt=\"Kệ sách trong thư viện\"]')?.naturalWidth > 0"))
        assert.equal(await evaluate("document.querySelector('h1').getBoundingClientRect().right > document.querySelector('figure img').getBoundingClientRect().left"), true)
        assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true)
        assert.equal(await evaluate("/Ảnh minh họa|Nghiên cứu & phát triển|[↗↓→]/.test(document.querySelector('main').textContent)"), false)
        await capture('overview-horizontal-desktop')
        await evaluate("document.getElementById('overview-content').scrollIntoView(); undefined")
        await capture('overview-research-desktop')
        await evaluate("document.querySelector('main').scrollTop = 0; undefined")
        await evaluate("history.pushState({}, '', '/overview/process'); window.dispatchEvent(new PopStateEvent('popstate'))")
        await waitFor(() => evaluate("document.querySelector('h1')?.textContent.includes('đến kết quả tìm kiếm')"))
        assert.equal(await evaluate("document.querySelector('details').open"), false)
        await evaluate("document.querySelector('details summary').click()")
        assert.equal(await evaluate("document.querySelector('details').open"), true)
        await evaluate("document.querySelector('details summary').click()")
        assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true)
        await capture('overview-process-desktop')
        await evaluate("document.getElementById('overview-content').scrollIntoView(); undefined")
        await capture('overview-workflow-desktop')
        await evaluate("document.querySelector('main').scrollTop = 0; undefined")
        await evaluate("history.pushState({}, '', '/overview/summary'); window.dispatchEvent(new PopStateEvent('popstate'))")
        await waitFor(() => evaluate("document.querySelector('h1')?.textContent.includes('Quản lý và')"))
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true)
        await capture('overview-mobile')
        await call('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false })
        await call('Page.addScriptToEvaluateOnNewDocument', { source: `
            const originalInterval = window.setInterval, originalClear = window.clearInterval;
            window.setInterval = (fn, ms, ...args) => {
                if (ms === 300000) { window.fixtureAutoScan = fn; return 2147483000; }
                return originalInterval(fn, ms, ...args);
            };
            window.clearInterval = id => { if (id === 2147483000) window.fixtureAutoScan = null; else originalClear(id); };
            window.alert = window.confirm = () => { throw new Error('Native dialog forbidden'); };
        ` })
        await call('Page.navigate', { url: origin + '/library/list' })
        await waitFor(() => evaluate("!!document.querySelector('a[href=\"/library/add\"]')"))
        assert.equal(await evaluate("document.querySelector('a[href=\"/library/add\"]').textContent.replace(/\\s+/g, ' ').trim()"), '+ Thêm học liệu')
        assert.equal(await evaluate("document.querySelector('a[href=\"/library/add\"]').getBoundingClientRect().height >= 44"), true)
        await capture('library-add-desktop')
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate("document.querySelector('a[href=\"/library/add\"]').getBoundingClientRect().right <= innerWidth"), true)
        await capture('library-add-mobile')
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false })
        await call('Page.navigate', { url: origin + '/library/source-sync/reconciliation' })
        await waitFor(() => evaluate("!!document.querySelector('select[aria-label=\"Chọn luồng dữ liệu\"]')"))
        await evaluate("const stream = document.querySelector('select[aria-label=\"Chọn luồng dữ liệu\"]'); stream.value='files'; stream.dispatchEvent(new Event('change', { bubbles: true }))")
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(button => button.textContent === 'Quét đối soát' && !button.disabled)"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).filter(button => button.textContent === 'Quét đối soát').length"), 1)
        await click('Quét đối soát')
        await waitFor(() => evaluate("document.body.textContent.includes('Đang đối soát nền:')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).find(button => button.textContent.includes('Đang đối soát'))?.disabled"), true)
        await waitFor(() => evaluate("!!document.querySelector('dialog[open]')"))
        assert.equal(reconciliationBodies.length, 1)
        assert.equal(reconciliationBodies[0].dry_run, true)
        await waitFor(() => evaluate("document.body.textContent.includes('Chưa bật truy xuất nguồn')"))
        assert.equal(await evaluate("document.body.textContent.includes('Chưa xác minh')"), true)
        assert.equal(await evaluate("document.body.textContent.includes('Tự phục hồi')"), false)
        await capture('reconciliation-dialog')
        await click('Đóng')
        await evaluate("document.querySelector('input[type=checkbox]').click()")
        await waitFor(() => evaluate("typeof window.fixtureAutoScan === 'function'"))
        await evaluate("for (let i = 0; i < 10; i++) window.fixtureAutoScan()")
        await waitFor(() => reconciliationBodies.length === 2)
        assert.equal(reconciliationBodies[1].dry_run, true)
        await waitFor(() => evaluate("!!document.querySelector('dialog[open]')"))
        await click('Đóng')
        await evaluate("history.pushState({}, '', '/overview/summary'); window.dispatchEvent(new PopStateEvent('popstate'))")
        await waitFor(() => evaluate("window.fixtureAutoScan === null"))
        // Unicode/mixed-language transport through every real React method.
        // Fixture responses attest UI/API mapping, not semantic relevance.
        mode = 'results'
        modelState = 'READY'
        for (const query of ['quang hợp'.normalize('NFD'), 'photosynthesis', 'base de données',
            'quang hợp photosynthesis photosynthèse']) {
            for (const method of ['bow', 'vector', 'hybrid']) {
                const previous = postCount
                const criteria = new URLSearchParams({ q: query, method, page: '1', size: '5' })
                await call('Page.navigate', { url: origin + '/search/query-methods?' + criteria })
                await waitFor(() => postCount > previous)
                await waitFor(() => evaluate("!!document.querySelector('article')"))
                assert.equal(bodies.at(-1).query, query.normalize('NFC'))
                assert.equal(bodies.at(-1).method, method)
                assert.equal(await evaluate("document.querySelector('#search-query').value"), query.normalize('NFC'))
            }
        }
    } finally {
        releaseSearch?.()
        if (ws) ws.close()
        browser.kill()
        await new Promise(resolve => server.close(resolve))
        await delay(400)
        await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
    }
})

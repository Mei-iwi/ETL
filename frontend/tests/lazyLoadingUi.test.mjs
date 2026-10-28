// Built React + Edge against isolated HTTP fixtures. No user DB, files, models or Drive.
import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { extname, join, resolve, sep } from 'node:path'

const root = resolve(process.env.ETL_LAZY_DIST || '.test-tmp/lazy-loading-review/after-dist')
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const baseline = process.env.ETL_LAZY_BASELINE === '1'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const runId = '01M4ZXS1XJ2B4H42CFY24KJY8C'

test('lazy panels keep metadata, defer DOM/details, stop hidden polling and retain read-only behavior', {
    skip: !existsSync(executable) || !existsSync(join(root, 'index.html')), timeout: 90000,
}, async () => {
    const requests = [], responses = []
    let pendingTranslation = true
    let generation = 'run-1'
    const bags = Array.from({ length: 100 }, (_, n) => ({ id: 'b' + n, title: 'Bag ' + n,
        unit_type: 'PAGE_TEXT', representation_mode: 'fallback', keywords: ['fixture'], vector_ready: true,
        embedding_dimension: 384, embedding_model: 'fixture', embedding_text: 'fixture '.repeat(100) }))
    const observations = Array.from({ length: 25 }, (_, n) => ({ query_id: 'q' + n, method: 'bow', latency_ms: 1,
        error_code: null, ranked: [{ resource_id: 'r1', resource_version_id: 'v1', score: 1 }] }))
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://fixture')
        if (url.pathname.startsWith('/api/')) requests.push({ path: url.pathname, query: url.search, method: req.method })
        const json = value => {
            const body = JSON.stringify(value)
            responses.push({ path: url.pathname, query: url.search, bytes: Buffer.byteLength(body) })
            res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(body)
        }
        if (url.pathname === '/api/v1/auth/config') return json({ mode: 'legacy_local' })
        if (url.pathname === '/api/v1/evaluations/runs') return json({ items: [], total: 0, total_pages: 1, page: 1, size: 7 })
        if (url.pathname === '/api/v1/evaluations/runs/' + runId) {
            const offset = Number(url.searchParams.get('offset') || 0)
            return json({ id: runId, dataset_id: runId, dataset_name: 'Fixture evaluation', status: 'COMPLETED',
                dataset_hash: 'fixture', corpus_fingerprint: 'fixture', warnings: [], relevance_threshold: 1,
                created_at: '2026-10-28T00:00:00Z', queries: observations.map(row => ({ query_id: row.query_id, query: row.query_id, relevance: [] })),
                snapshot: { model: { id: 'fixture', version: 'fixture', dimension: 384 } }, summary: {},
                observations: url.searchParams.get('include_observations') === 'false' ? [] : observations.slice(offset, offset + 20),
                pagination: { total: observations.length, offset, limit: 20 } })
        }
        if (url.pathname.endsWith('/bilingual')) return json({ original_file_url: '/api/v1/resource-versions/v1/processing-source',
            original_filename: 'fixture.mp4', options: { translation_pending: pendingTranslation }, extraction: [],
            items: Array.from({ length: 500 }, (_, n) => ({ segment_id: 's' + n, source_type: 'speech', source_language: 'vi',
                target_language: 'en', original_text: 'Original ' + n, translated_text: 'Translated ' + n, translation_status: 'completed',
                start_time: n, end_time: n + 1, generated: false })) })
        if (url.pathname === '/api/v1/resources') return json({ items: [{ id: 'r1', title: 'Fixture resource' }], pagination: { total: 1 } })
        if (url.pathname === '/api/v1/resources/r1/versions') return json([{ id: 'v1', resource_id: 'r1', version_number: 1, created_at: '2026-10-28' }])
        if (url.pathname === '/api/v1/resources/r1') return json({ id: 'r1', title: 'Fixture resource', provider_name: 'Fixture',
            resource_type_code: 'DOC', resource_type_name_vi: 'Document', source_origin: 'LOCAL', subjects: [], grade_levels: [],
            publication_status: 'PUBLISHED', is_active: true, updated_at: '2026-10-28', created_at: '2026-10-28' })
        if (url.pathname.endsWith('/description')) return json({ description: '', tags: [], can_edit: false })
        if (url.pathname.endsWith('/index-status')) return json({ resource_version_id: 'v1', processing_status: 'CONTENT_READY',
            index_status: 'READY', auto_enabled: false, latest_run_id: generation, published_run_id: generation,
            chunk_count: 100, chunk_preview_count: 100, bag_count: 100, representation_mode: 'fallback',
            representation_unit: 'content_bag', chunk_storage: 'transient', bow_ready: true, vector_ready: true,
            algorithm: 'fixture', chunk_size_words: 20, chunk_overlap_words: 5, embedding_model: 'fixture',
            embedding_dimension: 384, message: '', bags: url.searchParams.get('include_bags') === 'false' ? [] : bags })
        if (url.pathname.endsWith('/file')) { res.writeHead(200, { 'Content-Type': 'text/plain' }); return res.end('Preview fixture') }
        if (url.pathname.startsWith('/api/')) { res.writeHead(404); return res.end() }
        try {
            const path = url.pathname.startsWith('/assets/') ? resolve(root, '.' + url.pathname) : join(root, 'index.html')
            assert.ok(path.startsWith(root + sep))
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' }[extname(path)] || 'application/octet-stream' })
            res.end(await readFile(path))
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const profile = await mkdtemp(join(tmpdir(), 'etl-lazy-ui-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, serial = 0
    const pending = new Map()
    async function waitFor(read) {
        for (let n = 0; n < 120; n++) { const value = await read(); if (value) return value; await delay(100) }
        throw new Error('Lazy fixture timed out')
    }
    function call(method, params = {}) {
        const id = ++serial
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP ' + method)) }, 10000)
            pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(result.exceptionDetails, undefined)
        return result.result.value
    }
    const open = path => call('Page.navigate', { url: origin + path })
    const count = ending => requests.filter(row => row.path.endsWith(ending)).length
    const visible = value => evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:${!value}}); document.dispatchEvent(new Event('visibilitychange'))`)
    const metrics = {}
    try {
        const port = await waitFor(async () => { try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return 0 } })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const value = JSON.parse(event.data)
            const handler = pending.get(value.id)
            if (handler) { pending.delete(value.id); if (value.error) handler.reject(value.error); else handler.resolve(value.result) }
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        await call('Page.addScriptToEvaluateOnNewDocument', { source: `
            window.fixtureURLs = { created: 0, revoked: 0 };
            const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
            URL.createObjectURL = blob => { window.fixtureURLs.created++; return create(blob) };
            URL.revokeObjectURL = value => { window.fixtureURLs.revoked++; return revoke(value) };
        ` })
        await open('/library/processing?versionId=v1')
        await waitFor(() => evaluate("document.body.textContent.includes('TXT bản dịch')"))
        metrics.bilingual_closed_rows = await evaluate("document.querySelectorAll('article').length")
        assert.equal(metrics.bilingual_closed_rows, baseline ? 500 : 0)
        if (!baseline) {
            await evaluate(`const language = document.querySelector('input[aria-label="Ngôn ngữ bản dịch"]');
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(language,'de');
                language.dispatchEvent(new Event('input',{bubbles:true}))`)
            await evaluate("document.querySelector('summary[aria-controls=bilingual-segments]').click()")
            await waitFor(() => evaluate("document.querySelectorAll('article').length === 20"))
            await evaluate("Array.from(document.querySelectorAll('button')).find(button => button.textContent === 'Tiếp').click()")
            await waitFor(() => evaluate("document.querySelector('article')?.textContent.includes('Original 20')"))
            await evaluate("document.querySelector('summary[aria-controls=bilingual-segments]').click()")
            await waitFor(() => evaluate("document.querySelectorAll('article').length === 0"))
        }
        await visible(false)
        const beforeHidden = count('/bilingual')
        await delay(4300)
        metrics.bilingual_hidden_requests = count('/bilingual') - beforeHidden
        assert.equal(metrics.bilingual_hidden_requests, baseline ? 1 : 0)
        pendingTranslation = false
        if (!baseline) {
            await visible(true)
            await waitFor(() => count('/bilingual') === beforeHidden + 1)
            await delay(4300)
            assert.equal(count('/bilingual'), beforeHidden + 1, 'terminal translation does not poll')
            assert.equal(await evaluate("document.querySelector('input[aria-label=\"Ngôn ngữ bản dịch\"]').value"), 'de', 'unsaved options survive collapse and background refresh')
        }
        await open('/experiments/results?run_id=' + runId)
        await waitFor(() => evaluate("document.body.textContent.includes('Fixture evaluation')"))
        metrics.evaluation_closed_ranked = await evaluate("document.querySelectorAll('ol li').length")
        assert.equal(metrics.evaluation_closed_ranked, baseline ? 20 : 0)
        metrics.evaluation_initial_bytes = responses.filter(row => row.path.endsWith(runId)).at(-1).bytes
        if (!baseline) {
            await evaluate("Array.from(document.querySelectorAll('summary')).find(el => el.textContent === 'Chi tiết theo truy vấn').click()")
            await waitFor(() => evaluate("document.querySelectorAll('ol li').length === 20"))
            await evaluate("Array.from(document.querySelectorAll('#evaluation-observations button')).find(el => el.textContent === 'Tiếp').click()")
            await waitFor(() => evaluate("document.querySelectorAll('ol li').length === 5"))
        }
        await open('/library/indexing?resourceId=r1&versionId=v1')
        await waitFor(() => evaluate("document.body.textContent.includes('ContentBag') && !!document.querySelector('[data-index-panel]')"))
        await waitFor(() => count('/index-status') > 0)
        await delay(100)
        metrics.index_initial_bytes = responses.filter(row => row.path.endsWith('/index-status')).at(-1).bytes
        if (!baseline) assert.ok(requests.filter(row => row.path.endsWith('/index-status')).every(row => row.query.includes('include_bags=false')))
        await evaluate("Array.from(document.querySelectorAll('a')).find(el => el.getAttribute('href')?.includes('view=bags')).click()")
        await waitFor(() => evaluate("document.body.textContent.includes('Bag 0')"))
        assert.equal(await evaluate("document.querySelector('[data-index-panel=bags] tbody').children.length"), 10)
        if (!baseline) {
            generation = 'run-2'
            await evaluate("Array.from(document.querySelectorAll('a')).find(el => el.getAttribute('href')?.includes('view=index')).click()")
            await evaluate("Array.from(document.querySelectorAll('a')).find(el => el.getAttribute('href')?.includes('view=bags')).click()")
            await waitFor(() => evaluate("document.body.textContent.includes('Chỉ mục đã thay đổi')"))
            assert.equal(await evaluate("document.querySelector('[data-index-panel=bags] tbody') === null"), true)
        }
        await open('/library/r1?versionId=v1')
        await waitFor(() => evaluate("Array.from(document.querySelectorAll('button')).some(el => el.textContent === 'Xem tệp' && !el.disabled)"))
        assert.equal(count('/file'), 0, 'no preview bytes before explicit action')
        await visible(false)
        const beforeStatus = count('/index-status')
        await delay(5300)
        metrics.file_hidden_requests = count('/index-status') - beforeStatus
        assert.equal(metrics.file_hidden_requests, baseline ? 1 : 0)
        if (!baseline) { await visible(true); await waitFor(() => count('/index-status') === beforeStatus + 1) }
        await evaluate("Array.from(document.querySelectorAll('button')).find(el => el.textContent === 'Xem tệp').click()")
        await waitFor(() => evaluate("document.querySelector('pre')?.textContent.includes('Preview fixture')"))
        assert.equal(count('/file'), 1)
        await evaluate("Array.from(document.querySelectorAll('button')).find(el => el.textContent === 'Đóng tệp').click()")
        await waitFor(() => evaluate("window.fixtureURLs.revoked === 1"))
        assert.equal(await evaluate("window.fixtureURLs.created"), 1)
        assert.ok(requests.every(row => row.method === 'GET'), 'opening panels never mutates')
        console.log(JSON.stringify({ baseline, fixture: 'G2-G5', ...metrics }))
    } finally {
        ws?.close(); browser.kill(); server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
    }
})

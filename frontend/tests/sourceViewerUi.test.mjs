import assert from 'node:assert/strict'
import test from 'node:test'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { basename, dirname, extname, join, resolve, sep } from 'node:path'

const dist = resolve(process.env.ETL_TEST_DIST || 'frontend/dist')
const browserPath = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const previewUrl = 'https://drive.google.com/file/d/viewer-fixture/preview'
const viewUrl = 'https://drive.google.com/file/d/viewer-fixture/view'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('source viewer loads inside the page under the deployed CSP before OCR/index readiness', {
    skip: !existsSync(browserPath) || !existsSync(join(dist, 'index.html')), timeout: 60000,
}, async () => {
    const configurations = await Promise.all(['nginx.conf', 'nginx.auth.template'].map(name => readFile(`deploy/docker/${name}`, 'utf8')))
    const policies = configurations.map(text => /add_header Content-Security-Policy "([^"]+)"/.exec(text)[1])
    for (const policy of policies) {
        const frames = /frame-src ([^;]+)/.exec(policy)[1].split(' ')
        assert.ok(frames.includes('https://drive.google.com') && frames.includes('https://docs.google.com'))
        assert.ok(!frames.includes('*') && !frames.includes('https:'))
    }
    let releaseOpen, openFailed = false, openRequests = 0
    const server = createServer(async (req, res) => {
        const path = new URL(req.url, 'http://127.0.0.1').pathname
        const json = (body, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)) }
        if (req.method !== 'GET') return json({}, 405)
        if (path === '/api/v1/auth/config') return json({ mode: 'legacy_local' })
        if (path === '/health') return json({ status: 'ok', database: 'ok' })
        if (path === '/api/v1/resources/r1') return json({ id: 'r1', title: 'Viewer fixture', source_origin: 'POSTGRESQL',
            publication_status: 'PUBLISHED', is_active: true, subjects: [], grade_levels: [], created_at: '2026-01-01', updated_at: '2026-01-01' })
        if (path === '/api/v1/resources/r1/versions') return json([{ id: 'v1', version_number: 1, created_at: '2026-01-01' }])
        if (path === '/api/v1/resource-versions/v1/index-status') return json({ processing_status: 'NOT_STARTED', index_status: 'NOT_STARTED', metadata_only: true })
        if (path === '/api/v1/resources/r1/open') {
            openRequests++
            await new Promise(resolve => { releaseOpen = resolve })
            if (openFailed) return json({ detail: { code: 'SOURCE_UNAVAILABLE' } }, 503)
            return json({ master_resource_id: 'r1', version_id: 'v1', status: 'EXTERNAL_VIEWER_AVAILABLE',
            files: [{ source_file_id: 'viewer-fixture', file_name: 'Source.pdf', mime_type: 'application/pdf', revision: '1',
                access_status: 'ALLOWED', viewer_url: viewUrl, embedded_url: previewUrl }] })
        }
        if (path.startsWith('/api/')) return json({}, 404)
        const target = path.startsWith('/assets/') ? resolve(dist, '.' + path) : join(dist, 'index.html')
        assert.ok(target.startsWith(dist + sep))
        res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png' }[extname(target)] || 'application/octet-stream',
            'Content-Security-Policy': policies[0] })
        res.end(await readFile(target))
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const profile = await mkdtemp(join(tmpdir(), 'etl-source-viewer-'))
    const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let socket, nextId = 0, previewRequests = 0
    const pending = new Map()
    function call(method, params = {}) {
        const id = ++nextId
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 10000)
            pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value) }, reject: error => { clearTimeout(timer); reject(error) } })
            socket.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const result = await call('Runtime.evaluate', { expression, returnByValue: true })
        assert.equal(result.exceptionDetails, undefined)
        return result.result.value
    }
    async function waitFor(action) {
        for (let i = 0; i < 150; i++) { const value = await action(); if (value) return value; await delay(100) }
        throw new Error('Source viewer browser check timed out')
    }
    try {
        const port = await waitFor(async () => { try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null } })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        socket = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        socket.addEventListener('message', event => {
            const message = JSON.parse(event.data)
            const item = pending.get(message.id)
            if (item) { pending.delete(message.id); if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result) }
            if (message.method === 'Fetch.requestPaused') {
                assert.equal(message.params.request.url, previewUrl)
                previewRequests++
                // A provider document executes and posts from its actual origin;
                // no external Google account or SOURCE data is used by this test.
                void call('Fetch.fulfillRequest', { requestId: message.params.requestId, responseCode: 200,
                    responseHeaders: [{ name: 'Content-Type', value: 'text/html' }],
                    body: Buffer.from('<h1>Source preview fixture</h1><script>parent.postMessage("SOURCE_VIEWER_READY", "*")</script>').toString('base64') })
            }
        })
        await new Promise(resolve => socket.addEventListener('open', resolve, { once: true }))
        await call('Page.enable')
        await call('Fetch.enable', { patterns: [{ urlPattern: 'https://drive.google.com/*', requestStage: 'Request' }] })
        await call('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/library/r1` })
        const pendingButton = 'Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Đang tải…")'
        await waitFor(() => evaluate(`Boolean(${pendingButton}?.disabled && ${pendingButton}?.getAttribute("aria-busy") === "true")`))
        assert.equal(previewRequests, 0)
        assert.equal(await evaluate('Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Kiểm tra lại").disabled'), true)
        await waitFor(() => Boolean(releaseOpen))
        releaseOpen()
        await waitFor(() => evaluate('Array.from(document.querySelectorAll("button")).some(b => b.textContent === "Xem tệp" && !b.disabled)'))
        await evaluate('window.addEventListener("message", event => { if (event.origin === "https://drive.google.com" && event.data === "SOURCE_VIEWER_READY") window.sourcePreviewLoaded = true }); Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Xem tệp").click()')
        await waitFor(() => evaluate('window.sourcePreviewLoaded === true'))
        assert.equal(previewRequests, 1)
        assert.equal(await evaluate('document.querySelector("#source-file-preview").src'), previewUrl)
        assert.equal(await evaluate('document.querySelector("#source-file-preview").hasAttribute("sandbox")'), false)
        assert.equal(await evaluate('Array.from(document.querySelectorAll("a")).find(a => a.textContent.includes("Xem tệp bên ngoài")).href'), viewUrl)
        await evaluate('Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Đóng tệp").click()')
        await waitFor(() => evaluate('!document.querySelector("#source-file-preview")'))
        openFailed = true
        await evaluate('Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Kiểm tra lại").click()')
        await waitFor(() => openRequests === 2)
        await waitFor(() => evaluate(`Boolean(${pendingButton}?.disabled)`))
        assert.equal(await evaluate('Array.from(document.querySelectorAll("a")).some(a => a.href === ' + JSON.stringify(viewUrl) + ')'), false)
        releaseOpen()
        await waitFor(() => evaluate('Boolean(document.querySelector("section[aria-labelledby=resource-file-title] [role=alert]"))'))
        assert.equal(await evaluate(`Boolean(${pendingButton})`), false)
        assert.equal(await evaluate('Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Xem tệp").disabled'), true)
        openFailed = false
        await evaluate('Array.from(document.querySelectorAll("button")).find(b => b.textContent === "Kiểm tra lại").click()')
        await waitFor(() => openRequests === 3)
        await waitFor(() => evaluate(`Boolean(${pendingButton}?.disabled)`))
        releaseOpen()
        await waitFor(() => evaluate('Array.from(document.querySelectorAll("button")).some(b => b.textContent === "Xem tệp" && !b.disabled)'))
        assert.equal(await evaluate('Boolean(document.querySelector("section[aria-labelledby=resource-file-title] [role=alert]"))'), false)
    } finally {
        if (socket?.readyState === WebSocket.OPEN) { socket.send(JSON.stringify({ id: ++nextId, method: 'Browser.close' })); socket.close() }
        browser.kill()
        server.closeAllConnections()
        await new Promise(resolve => server.close(resolve))
        assert.equal(dirname(profile), tmpdir())
        assert.ok(basename(profile).startsWith('etl-source-viewer-'))
        await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
    }
})

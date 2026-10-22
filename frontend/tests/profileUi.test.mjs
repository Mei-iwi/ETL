// Real built React UI against isolated fixtures; never connects to a business DB.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { extname, join, resolve, sep } from 'node:path'
import test from 'node:test'

const root = process.env.ETL_T4_FRONTEND_ROOT || resolve('frontend')
const executable = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('personal profile layout, own updates, conflict, failure, back and mobile', {
    skip: !existsSync(executable) || !existsSync(join(root, 'dist/index.html')), timeout: 90000,
}, async () => {
    let conflict = false, unavailable = false, editable = true, actor = 'A', admin = false
    const writes = [], exceptions = []
    let data = { full_name: 'Nguyễn Minh Anh', email: 'user@etl.test', role_name: 'Giảng viên', access_role: 'VIEWER',
        is_active: true, email_verified: true, created_at: '2026-01-01T00:00:00Z', subject_name: 'Toán học',
        organization_name: 'Trường Đại học', address: 'Hà Nội', bio: 'Chia sẻ kiến thức, kết nối học liệu.',
        updated_at: '2026-10-23T00:00:00Z', revision: 'a'.repeat(64), editable: true, unavailable_reason: null }
    const server = createServer(async (req, res) => {
        const url = new URL(req.url, 'http://localhost')
        const json = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)) }
        if (url.pathname === '/health') return json({ status: 'ok' })
        if (url.pathname === '/api/v1/auth/config') return json({ mode: 'master_users', login_required: true, admin_feature_enabled: true })
        if (url.pathname === '/api/v1/auth/me') return json({ id: 'master:user-' + actor, role: admin ? 'ADMIN' : 'VIEWER', roles: [admin ? 'admin' : 'viewer'], mode: 'master_users',
            display_name: data.full_name, email: data.email, admin_feature_enabled: true, resource_ids: ['resource-' + actor] })
        if (url.pathname === '/api/v1/resource-types') return json({ items: [{ code: 'PDF', name: 'PDF' }], pagination: { page: 1, size: 100, total: 1, total_pages: 1 } })
        if (url.pathname === '/api/v1/resources') return json({ items: [{ id: 'resource-' + actor, title: 'Tài liệu của ' + actor,
            provider_name: 'Đơn vị', resource_type_name_vi: 'PDF', publication_status: 'PUBLISHED', updated_at: '2026-10-23T00:00:00Z' }],
            pagination: { page: 1, size: 7, total: 1, total_pages: 1 } })
        if (url.pathname === '/api/v1/auth/csrf') return json({ csrf_token: 'isolated-profile-csrf' })
        if (url.pathname === '/api/v1/auth/profile') {
            if (unavailable) return json({ detail: { code: 'PROFILE_UNAVAILABLE', message: 'PRIVATE_INTERNAL' } }, 503)
            if (req.method === 'PATCH') {
                let raw = ''; for await (const chunk of req) raw += chunk
                const body = JSON.parse(raw); writes.push(body)
                assert.equal(req.headers['x-csrf-token'], 'isolated-profile-csrf')
                if (conflict) return json({ detail: { code: 'PROFILE_CHANGED' } }, 409)
                data = { ...data, ...body, revision: 'b'.repeat(64) }
            }
            return json({ ...data, editable, unavailable_reason: editable ? null : 'PROFILE_NOT_LINKED' })
        }
        if (url.pathname.startsWith('/api/')) return json({ detail: 'not found' }, 404)
        const file = url.pathname.startsWith('/assets/') ? resolve(root, 'dist', '.' + url.pathname) : join(root, 'dist/index.html')
        if (!file.startsWith(join(root, 'dist') + sep)) { res.writeHead(403); return res.end() }
        try {
            res.writeHead(200, { 'Content-Type': { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png', '.svg': 'image/svg+xml' }[extname(file)] || 'application/octet-stream' })
            res.end(await readFile(file))
        } catch { res.writeHead(404); res.end() }
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const origin = `http://127.0.0.1:${server.address().port}`
    const browserDirectory = await mkdtemp(join(tmpdir(), 'etl-profile-browser-'))
    const browser = spawn(executable, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
        '--remote-debugging-port=0', `--user-data-dir=${browserDirectory}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map()
    async function wait(fn) {
        for (let i = 0; i < 150; i++) { const result = await fn(); if (result) return result; await delay(100) }
        throw new Error('Profile assertion timed out')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)) }, 15000)
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
    const open = () => call('Page.navigate', { url: origin + '/account/profile' })
    const fill = (id, value, kind = 'HTMLTextAreaElement') => evaluate(`Object.getOwnPropertyDescriptor(${kind}.prototype, 'value').set.call(document.getElementById(${JSON.stringify(id)}), ${JSON.stringify(value)}); document.getElementById(${JSON.stringify(id)}).dispatchEvent(new Event('input', { bubbles: true }))`)
    const evidence = resolve(root, '../profile-evidence')
    async function capture(name) {
        await mkdir(evidence, { recursive: true })
        const result = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
        await writeFile(join(evidence, name + '.png'), Buffer.from(result.data, 'base64'))
    }
    try {
        const port = await wait(async () => { try { return Number((await readFile(join(browserDirectory, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null } })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const message = JSON.parse(event.data), item = pending.get(message.id)
            if (item) { pending.delete(message.id); if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result) }
            if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params)
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable'); await call('Runtime.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false })
        await open(); await wait(() => evaluate("!!document.querySelector('.profile-card')"))
        assert.equal(await evaluate("!!document.querySelector('.app-header') && !document.querySelector('.sub-menu')"), true)
        assert.equal(await evaluate("document.querySelector('.profile-avatar').complete && document.querySelector('.profile-avatar').naturalWidth > 0"), true)
        await evaluate("document.querySelector('.header-account summary').click()")
        assert.equal(await evaluate("document.querySelector('.header-account-panel').textContent.indexOf('Thông tin cá nhân') < document.querySelector('.header-account-panel').textContent.indexOf('Đăng xuất')"), true)
        await click('Thông tin cá nhân')
        await capture('profile-desktop')
        await click('Cập nhật thông tin')
        await fill('profile-organization', 'Chưa lưu', 'HTMLInputElement')
        await click('Hủy')
        assert.equal(writes.length, 0)
        assert.equal(await evaluate("document.querySelector('.profile-about').textContent.includes('Trường Đại học')"), true)
        await click('Cập nhật thông tin')
        assert.equal(await evaluate("!!document.querySelector('#profile-organization') && !document.querySelector('input[type=email]')"), true)
        await fill('profile-organization', ' Trường cập nhật ', 'HTMLInputElement')
        await fill('profile-bio', 'Hồ sơ đã cập nhật')
        await evaluate("for(let i=0;i<5;i++) document.querySelector('.profile-form').requestSubmit()")
        await wait(() => evaluate("document.body.textContent.includes('Đã cập nhật thông tin cá nhân.')"))
        assert.equal(writes.length, 1); assert.equal(writes[0].organization_name, 'Trường cập nhật')
        await click('Cập nhật thông tin'); conflict = true
        await fill('profile-bio', 'Xung đột')
        await click('Lưu thay đổi')
        await wait(() => evaluate("document.body.textContent.includes('Thông tin đã thay đổi ở nơi khác')"))
        assert.equal(await evaluate("document.querySelector('.profile-form button[type=submit]').disabled"), true)
        conflict = false; await click('Tải lại hồ sơ')
        await wait(() => evaluate("!document.querySelector('.profile-form') && !!document.querySelector('.profile-about')"))
        await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
        await capture('profile-mobile')
        unavailable = true; await open(); await wait(() => evaluate("!!document.querySelector('[role=alert]')"))
        assert.equal(await evaluate("document.body.textContent.includes('PRIVATE_INTERNAL')"), false)
        unavailable = false; editable = false; await click('Tải lại hồ sơ')
        await wait(() => evaluate("!!document.querySelector('.profile-card')"))
        assert.equal(await evaluate("Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Cập nhật thông tin')"), false)
        await click('Quay lại')
        await wait(() => evaluate("location.pathname === '/overview/summary'"))
        await call('Page.navigate', { url: origin + '/library/list' })
        await wait(() => evaluate("document.body?.textContent.includes('Tài liệu của A')"))
        actor = 'B'
        await evaluate("window.dispatchEvent(new Event('focus'))")
        await wait(() => evaluate("document.body.textContent.includes('Tài liệu của B')"))
        assert.equal(await evaluate("document.body.textContent.includes('Tài liệu của A')"), false,
            'Old account documents must disappear after the authenticated identity changes')
        assert.equal(await evaluate("!!document.querySelector('a[href$=\"/update-version\"]')"), false,
            'Viewer must not see an update action that the API denies')
        assert.equal(await evaluate("!!document.querySelector('a[href=\"/library/indexing\"]')"), false)
        assert.equal(await evaluate("!!document.querySelector('a[href=\"/experiments\"]')"), false)
        assert.equal(await evaluate("!!document.querySelector('a[href=\"/systems\"]')"), false)
        admin = true
        await evaluate("window.dispatchEvent(new Event('focus'))")
        await wait(() => evaluate("!!document.querySelector('a[href$=\"/update-version\"]')"))
        assert.equal(await evaluate("!!document.querySelector('a[href=\"/library/indexing\"]')"), true,
            'Admin retains management actions')
        for (const path of ['/library/add', '/library/source-sync', '/experiments', '/systems']) {
            assert.equal(await evaluate(`!!document.querySelector('a[href="${path}"]')`), true,
                `Admin should see ${path}`)
        }
        assert.deepEqual(exceptions, [])
    } finally {
        if (ws) ws.close()
        browser.kill()
        await new Promise(resolve => server.close(resolve))
        // Preserve the isolated browser profile and screenshots as test evidence.
    }
})

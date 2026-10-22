// Real React + Nginx + FastAPI + isolated PostgreSQL Auth; never a Dev database.
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'

const origin = process.env.ETL_DB_AUTH_QA_ORIGIN
const password = process.env.ETL_DB_AUTH_QA_PASSWORD
const userPassword = process.env.ETL_DB_AUTH_QA_USER_PASSWORD
const newPassword = process.env.ETL_DB_AUTH_QA_NEW_PASSWORD
const apiContainer = process.env.ETL_DB_AUTH_QA_API
const pgContainer = process.env.ETL_DB_AUTH_QA_POSTGRES
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('database login, account administration, roles, password change, restart and outage', {
    skip: !password || !origin, timeout: 180000,
}, async () => {
    assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/)
    assert.match(apiContainer, /^etl-db-auth-qa-/)
    assert.match(pgContainer, /^etl-auth-test-/)
    const output = resolve(process.env.ETL_DB_AUTH_QA_OUTPUT || '.test-tmp/database-auth/browser')
    await mkdir(output, { recursive: true })
    const profile = await mkdtemp(join(tmpdir(), 'etl-db-auth-browser-'))
    const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
        '--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu', '--remote-debugging-port=0',
        `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0, pgStopped = false
    const pending = new Map(), errors = []
    async function waitFor(fn) {
        for (let i = 0; i < 450; i++) { const value = await fn(); if (value) return value; await delay(100) }
        throw new Error('Database auth browser timed out; secret values withheld')
    }
    function call(method, params = {}) {
        const id = ++sequence
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)) }, 20000)
            pending.set(id, { resolve: result => { clearTimeout(timeout); resolve(result) }, reject })
            ws.send(JSON.stringify({ id, method, params }))
        })
    }
    async function evaluate(expression) {
        const response = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
        assert.equal(response.exceptionDetails, undefined, 'Browser expression failed; values withheld')
        return response.result.value
    }
    async function field(selector, value) {
        await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}))})()`)
    }
    async function login(email, value) {
        await waitFor(() => evaluate("!!document.querySelector('#login-email') && !document.querySelector('.etl-login-submit').disabled"))
        await field('#login-email', email); await field('#login-password', value)
        await evaluate("document.querySelector('.etl-login form').requestSubmit()")
        await waitFor(() => evaluate("location.pathname !== '/login'"))
    }
    async function logout() {
        await evaluate("document.querySelector('.header-account-trigger').click();document.querySelector('.header-account-logout').click()")
        await waitFor(() => evaluate("location.pathname === '/login'"))
    }
    async function shot(name) {
        const result = await call('Page.captureScreenshot', { format: 'png' })
        await writeFile(join(output, name + '.png'), Buffer.from(result.data, 'base64'))
    }
    try {
        const port = await waitFor(async () => {
            try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
        })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const message = JSON.parse(event.data), item = pending.get(message.id)
            if (item) { pending.delete(message.id); message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result) }
            if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text)
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable'); await call('Runtime.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
        await call('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }, { name: 'prefers-color-scheme', value: 'dark' }] })
        await call('Page.navigate', { url: origin + '/systems/users' })
        await waitFor(() => evaluate("!!document.querySelector('#login-email')"))
        assert.equal(await evaluate("getComputedStyle(document.querySelector('#login-title')).color"), 'rgb(248, 250, 252)')
        await shot('login-dark')
        await login('admin@etl.test', password)
        await waitFor(() => evaluate("!!document.querySelector('.account-form')"))
        await field('.account-form input[type=email]', 'qa-user@etl.test')
        await field('.account-form input:not([type])', 'QA User')
        await field('.account-form input[type=password]', userPassword)
        await evaluate("document.querySelector('.account-form').requestSubmit()")
        await waitFor(() => evaluate("!![...document.querySelectorAll('tbody tr')].find(row=>row.textContent.includes('qa-user@etl.test'))"))
        await waitFor(() => evaluate("!document.querySelector('.account-form button').disabled"))
        assert.equal(await evaluate("document.body.textContent.includes('$argon2id$')"), false)
        await shot('admin-users-desktop')
        await call('Emulation.setDeviceMetricsOverride', { width: 375, height: 812, deviceScaleFactor: 1, mobile: true })
        assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'), true)
        await shot('admin-users-mobile')
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
        await evaluate("[...document.querySelectorAll('tbody tr')].find(row=>row.textContent.includes('admin@etl.test')).querySelectorAll('button')[1].click()")
        await waitFor(() => evaluate("!!document.querySelector('.account-error')"))
        assert.equal(await evaluate("fetch('/api/v1/admin/users').then(r=>r.json()).then(v=>v.items.find(u=>u.email==='admin@etl.test').is_locked)"), false)
        await logout()
        await login('qa-user@etl.test', userPassword)
        await waitFor(() => evaluate("location.pathname === '/account/password'"))
        await field('.account-form input[autocomplete=current-password]', userPassword)
        await field('.account-form label:nth-child(2) input', newPassword)
        await field('.account-form label:nth-child(3) input', newPassword)
        await evaluate("document.querySelector('.account-form').requestSubmit()")
        await waitFor(() => evaluate("location.pathname === '/login'"))
        await login('qa-user@etl.test', newPassword)
        assert.equal(await evaluate("fetch('/api/v1/auth/me').then(r=>r.json()).then(v=>v.role)"), 'VIEWER')
        assert.equal(await evaluate("fetch('/api/v1/admin/users').then(r=>r.status)"), 403)
        assert.equal(await evaluate("fetch('/api/v1/resource-versions/no-grant/index',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.status)"), 403)
        await call('Page.navigate', { url: origin + '/systems/users' })
        await waitFor(() => evaluate("location.pathname === '/overview/summary'"))
        assert.equal(await evaluate("document.querySelector('.account-form') === null"), true)
        await logout()
        await login('admin@etl.test', password)
        await call('Page.navigate', { url: origin + '/systems/users' })
        await waitFor(() => evaluate("!![...document.querySelectorAll('tbody tr')].find(row=>row.textContent.includes('qa-user@etl.test'))"))
        await evaluate("[...document.querySelectorAll('tbody tr')].find(row=>row.textContent.includes('qa-user@etl.test')).querySelector('button').click()")
        await field('.account-form select', 'operator')
        await evaluate("document.querySelector('.account-form fieldset input').click();document.querySelector('.account-form').requestSubmit()")
        await waitFor(() => evaluate("[...document.querySelectorAll('tbody tr')].find(row=>row.textContent.includes('qa-user@etl.test'))?.textContent.includes('operator')"))
        await logout()
        await login('qa-user@etl.test', newPassword)
        assert.equal(await evaluate("fetch('/api/v1/master-data/sync/status').then(r=>r.status)"), 200)
        execFileSync('docker', ['restart', '-t', '60', apiContainer], { stdio: 'ignore', windowsHide: true })
        await waitFor(() => evaluate("fetch('/api/v1/auth/me').then(r=>r.status===200).catch(()=>false)"))
        execFileSync('docker', ['stop', '-t', '60', pgContainer], { stdio: 'ignore', windowsHide: true }); pgStopped = true
        assert.equal(await evaluate("fetch('/api/v1/auth/me').then(r=>r.status)"), 503)
        execFileSync('docker', ['start', pgContainer], { stdio: 'ignore', windowsHide: true }); pgStopped = false
        await waitFor(() => evaluate("fetch('/api/v1/auth/me').then(r=>r.status===200).catch(()=>false)"))
        assert.equal(await evaluate("fetch('/api/v1/auth/me').then(r=>r.json()).then(v=>v.role)"), 'OPERATOR')
        await logout()
        assert.deepEqual(errors, [])
        await writeFile(join(output, 'results.json'), JSON.stringify({ status: 'PASS', checks: [
            'real_postgres_login', 'admin_create_user', 'no_hash_in_ui', 'last_admin_protected', 'forced_password_change',
            'viewer_admin_and_index_denied', 'direct_url_guard', 'operator_explicit_sync', 'api_restart_keeps_session',
            'postgres_outage_fail_closed', 'postgres_restart_keeps_account_and_session', 'responsive', 'system_dark_mode', 'logout'] }, null, 2))
    } finally {
        if (pgStopped) execFileSync('docker', ['start', pgContainer], { stdio: 'ignore', windowsHide: true })
        if (ws?.readyState === 1) { await call('Browser.close').catch(() => {}); ws.close() } else browser.kill()
    }
})

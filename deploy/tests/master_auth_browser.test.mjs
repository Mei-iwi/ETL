import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'

const origin = process.env.ETL_MASTER_AUTH_QA_ORIGIN
const password = process.env.ETL_MASTER_AUTH_QA_PASSWORD
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
test('automatic MASTER/admin login has no account dropdown and preserves RBAC', {
    skip: !origin || !password, timeout: 90000,
}, async () => {
    assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/)
    const output = resolve(process.env.ETL_MASTER_AUTH_QA_OUTPUT)
    await mkdir(output, { recursive: true })
    const profile = await mkdtemp(join(tmpdir(), 'etl-master-auth-'))
    const browser = spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', [
        '--headless=new', '--no-first-run', '--disable-gpu', '--disable-extensions',
        '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
    let ws, sequence = 0
    const pending = new Map(), errors = []
    async function waitFor(fn) {
        for (let i = 0; i < 300; i++) { const value = await fn(); if (value) return value; await delay(100) }
        throw new Error('MASTER authentication UI timed out; values withheld')
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
        const response = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
        assert.equal(response.exceptionDetails, undefined, 'Browser expression failed; values withheld')
        return response.result.value
    }
    async function field(selector, value) {
        await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}))})()`)
    }
    try {
        const port = await waitFor(async () => { try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null } })
        const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
        ws = new WebSocket(pages.find(p => p.type === 'page').webSocketDebuggerUrl)
        ws.addEventListener('message', event => {
            const message = JSON.parse(event.data), item = pending.get(message.id)
            if (item) { pending.delete(message.id); message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result) }
            if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text)
        })
        await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
        await call('Page.enable'); await call('Runtime.enable')
        await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })
        await call('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
        await call('Page.navigate', { url: origin + '/login' })
        await waitFor(() => evaluate("!!document.querySelector('#login-email') && !document.querySelector('.etl-login-submit').disabled"))
        assert.equal(await evaluate("document.querySelector('#login-provider') === null"), true)
        const shot = await call('Page.captureScreenshot', { format: 'png' })
        await writeFile(join(output, 'automatic-login.png'), Buffer.from(shot.data, 'base64'))
        async function login(email) {
            await field('#login-email', email); await field('#login-password', password)
            await evaluate("document.querySelector('.etl-login form').requestSubmit()")
            await waitFor(() => evaluate("location.pathname !== '/login'"))
        }
        await login('master@etl.test')
        assert.equal(await evaluate("fetch('/api/v1/auth/me').then(r=>r.json()).then(v=>v.role)"), 'VIEWER')
        assert.equal(await evaluate("fetch('/api/v1/system/health').then(r=>r.status)"), 403)
        await evaluate("document.querySelector('.header-account-trigger').click();document.querySelector('.header-account-logout').click()")
        await waitFor(() => evaluate("!!document.querySelector('#login-email') && !document.querySelector('.etl-login-submit').disabled"))
        await login('admin@etl.test')
        assert.equal(await evaluate("fetch('/api/v1/auth/me').then(r=>r.json()).then(v=>v.role)"), 'ADMIN')
        assert.equal(await evaluate("fetch('/api/v1/system/health').then(r=>r.status)"), 200)
        await evaluate("document.querySelector('.header-account-trigger').click();document.querySelector('.header-account-logout').click()")
        await waitFor(() => evaluate("!!document.querySelector('#login-email') && !document.querySelector('.etl-login-submit').disabled"))
        await login('master@etl.test')
        const apiContainer = process.env.ETL_MASTER_AUTH_QA_API
        assert.match(apiContainer, /^etl-master-login-qa-/)
        execFileSync('docker', ['exec', apiContainer, 'python', '-c', "from pathlib import Path; Path('/run/etl/master-stale').touch()"], { windowsHide: true, stdio: 'ignore' })
        await call('Page.navigate', { url: origin + '/login' })
        await waitFor(() => evaluate("!!document.querySelector('#login-email') && !document.querySelector('.etl-login-submit').disabled"))
        await login('admin@etl.test')
        assert.equal(await evaluate("fetch('/api/v1/auth/me').then(r=>r.json()).then(v=>v.role)"), 'ADMIN')
        assert.deepEqual(errors, [])
        await writeFile(join(output, 'results.json'), JSON.stringify({ status: 'PASS', checks: ['no_account_dropdown', 'master_hex_salt_login', 'source_admin_not_promoted', 'automatic_admin_login', 'backend_rbac', 'logout', 'admin_login_after_stale_master_session', 'no_browser_exceptions'] }, null, 2))
    } finally {
        if (ws?.readyState === 1) { await call('Browser.close').catch(() => {}); ws.close() } else browser.kill()
    }
})

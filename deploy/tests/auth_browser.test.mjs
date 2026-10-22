// Real React + Nginx + FastAPI sessions. Only the isolated QA fixture is allowed.
import assert from 'node:assert/strict'
import { execFileSync, spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import test from 'node:test'

const origin = process.env.ETL_AUTH_TEST_ORIGIN || 'http://127.0.0.1:18088'
assert.ok(['http://127.0.0.1:18088', 'https://127.0.0.1:18443'].includes(origin))
const sessionName = origin.startsWith('https:') ? '__Host-etl_session' : 'etl_session'
const browserPath = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const mode = process.env.ETL_AUTH_TEST_MODE || 'single_admin'
test(`${mode}: real authentication and responsive UI`, {
  skip: !process.env.ETL_AUTH_TEST_PASSWORD || !existsSync(browserPath), timeout: 120000,
}, async () => {
  const config = await (await fetch(origin + '/api/v1/auth/config')).json()
  assert.equal(config.mode, mode)
  assert.equal((await fetch(origin + '/api/v1/resources?page=1&size=10&q=')).status, mode === 'single_admin' ? 401 : 200)
  assert.equal((await fetch(origin + '/api/v1/resources', { headers: { 'X-Forwarded-For': '127.0.0.1' } })).status, 403)
  assert.equal((await fetch(origin + '/api/v1/resources', { headers: { 'X-ETL-Proxy-Key': 'forged' } })).status, 403)
  const profile = await mkdtemp(join(tmpdir(), 'etl-auth-browser-'))
  const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--disable-extensions', '--disable-gpu',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { windowsHide: true, stdio: 'ignore' })
  let ws, sequence = 0
  const pending = new Map(), errors = []
  const output = resolve('.test-tmp/docker-portable/auth-browser')
  await mkdir(output, { recursive: true })
  async function waitFor(fn) {
    for (let i = 0; i < 200; i++) { const value = await fn(); if (value) return value; await delay(100) }
    throw new Error('Auth browser condition timed out')
  }
  function call(method, params = {}) {
    const id = ++sequence
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 15000)
      pending.set(id, { resolve: value => { clearTimeout(timeout); resolve(value) }, reject })
      ws.send(JSON.stringify({ id, method, params }))
    })
  }
  async function evaluate(expression) {
    const response = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    assert.equal(response.exceptionDetails, undefined, 'Browser expression failed (values withheld)')
    return response.result.value
  }
  async function screenshot(name) {
    const capture = await call('Page.captureScreenshot', { format: 'png' })
    await writeFile(join(output, name + '.png'), Buffer.from(capture.data, 'base64'))
  }
  async function field(selector, value) {
    await evaluate(`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(document.querySelector(${JSON.stringify(selector)}),${JSON.stringify(value)}); document.querySelector(${JSON.stringify(selector)}).dispatchEvent(new Event('input',{bubbles:true}))`)
  }
  try {
    const port = await waitFor(async () => {
      try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) } catch { return null }
    })
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
    ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
    ws.addEventListener('message', event => {
      const message = JSON.parse(event.data), item = pending.get(message.id)
      if (item) { pending.delete(message.id); if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result) }
      if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text)
    })
    await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
    await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable')
    if (origin.startsWith('https:')) await call('Security.setIgnoreCertificateErrors', { ignore: true }) // Synthetic fixture certificate only; HTTP client independently verifies its CA/SAN.
    await call('Emulation.setDeviceMetricsOverride', { width: 1366, height: 900, deviceScaleFactor: 1, mobile: false })
    await call('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] })
    await call('Page.navigate', { url: origin + '/library/list?from=auth-e2e' })
    if (mode === 'local_demo') {
      await waitFor(() => evaluate("location.pathname === '/library/list' && !!document.querySelector('.header-account')"))
      assert.equal(await evaluate("document.querySelector('#login-email') === null"), true)
      assert.equal(await evaluate("(async()=> (await (await fetch('/api/v1/auth/me')).json()).role)()"), 'ADMIN')
      assert.equal(await evaluate("new Promise(resolve=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/v1/auth/logout');xhr.onload=()=>resolve(xhr.status);xhr.send()})"), 403)
      await screenshot('local-demo-no-login')
      await writeFile(join(output, 'local-demo-evidence.json'), JSON.stringify({ status: 'PASS', checks: ['noLoginUI', 'serverPrincipalADMIN', 'csrfRequired', 'forgedProxyRejected'] }, null, 2))
      return
    }
    await waitFor(() => evaluate("location.pathname === '/login' && !!document.querySelector('#login-email') && !document.querySelector('.etl-login-submit').disabled"))
    assert.ok(await evaluate("new URLSearchParams(location.search).get('returnTo').includes('/library/list')"))
    await screenshot('login-desktop-light')
    assert.equal(await evaluate("document.querySelector('.etl-theme') === null"), true)
    assert.equal(await evaluate("!!document.querySelector('.etl-login-orbit')"), true)
    await call('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'dark' }] })
    assert.equal(await evaluate("getComputedStyle(document.querySelector('#login-title')).color"), 'rgb(248, 250, 252)')
    await screenshot('login-desktop-dark-preference')
    await call('Emulation.setDeviceMetricsOverride', { width: 320, height: 740, deviceScaleFactor: 1, mobile: true })
    assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
    await screenshot('login-mobile')
    await call('Emulation.setDeviceMetricsOverride', { width: 640, height: 450, deviceScaleFactor: 1, mobile: false })
    assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true)
    await screenshot('login-compact-viewport')
    await call('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
    await field('#login-email', 'admin@etl.test')
    await field('#login-password', 'invalid-generated-test-password')
    await evaluate("document.querySelector('.etl-password button').click()")
    assert.equal(await evaluate("document.querySelector('#login-password').type"), 'text')
    await evaluate("document.querySelector('.etl-password button').click(); document.querySelector('form').requestSubmit()")
    await waitFor(() => evaluate("document.querySelector('[role=alert]')?.textContent.includes('Email')"))
    assert.equal(await evaluate("document.activeElement.getAttribute('role')"), 'alert')
    assert.equal(await evaluate("document.querySelector('#login-password').value"), '')
    await screenshot('login-error')
    await field('#login-password', process.env.ETL_AUTH_TEST_PASSWORD)
    await evaluate("document.querySelector('.etl-remember input').click(); document.querySelector('#login-password').focus()")
    await call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: '\r', unmodifiedText: '\r' })
    await call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13 })
    await waitFor(() => evaluate("location.pathname === '/library/list' && !!document.querySelector('.header-account')"))
    const cookies = (await call('Network.getCookies', { urls: [origin] })).cookies
    const session = cookies.find(cookie => cookie.name === sessionName)
    assert.ok(session.httpOnly && session.sameSite === 'Strict' && session.expires > Date.now() / 1000)
    assert.equal(session.secure, origin.startsWith('https:'))
    assert.equal(await evaluate("document.cookie.includes('etl_session')"), false)
    assert.equal(await evaluate('localStorage.length + sessionStorage.length'), 0)
    assert.equal(await evaluate("(async()=> (await fetch('/api/v1/resources?page=1&size=10&q=')).status)()"), 200)
    assert.equal(await evaluate("new Promise(resolve=>{ const xhr=new XMLHttpRequest(); xhr.open('POST','/api/v1/auth/logout'); xhr.setRequestHeader('X-CSRF-Token','invalid'); xhr.onload=()=>resolve(xhr.status); xhr.send() })"), 403)
    assert.equal(await evaluate("(async()=> (await fetch('/api/v1/auth/me')).status)()"), 200)
    // Restart only the owned fixture API. Cookie and persistent session must survive.
    if (process.env.ETL_AUTH_TEST_DOCKER_CONFIG) {
      const args = ['--config', process.env.ETL_AUTH_TEST_DOCKER_CONFIG]
      const owner = execFileSync('docker', [...args, 'inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}', 'etl-auth-acceptance-api-1'], { encoding: 'utf8', windowsHide: true }).trim()
      assert.equal(owner, 'etl-auth-acceptance')
      execFileSync('docker', [...args, 'restart', '--time', '10', 'etl-auth-acceptance-api-1'], { stdio: 'ignore', windowsHide: true })
      await waitFor(async () => { try { return (await fetch(origin + '/health')).status === 200 } catch { return false } })
      assert.equal(await evaluate("(async()=> (await fetch('/api/v1/auth/me')).status)()"), 200)
    }
    await evaluate("document.querySelector('.header-account-trigger').click(); document.querySelector('.header-account-logout').click()")
    await waitFor(() => evaluate("location.pathname === '/login' && !!document.querySelector('#login-email')"))
    assert.equal(await evaluate("(async()=> (await fetch('/api/v1/auth/me')).status)()"), 401)
    await field('#login-email', 'admin@etl.test'); await field('#login-password', process.env.ETL_AUTH_TEST_PASSWORD)
    await evaluate("document.querySelector('form').requestSubmit()")
    await waitFor(() => evaluate("!!document.querySelector('.header-account')"))
    // Expire only this fixture browser cookie; next protected response clears the UI.
    await call('Network.deleteCookies', { name: sessionName, url: origin })
    assert.equal(await evaluate("(async()=> (await fetch('/api/v1/resources?page=1&size=10&q=')).status)()"), 401)
    await waitFor(() => evaluate("location.pathname === '/login' && !document.querySelector('.app-shell')"))
    await call('Page.navigate', { url: origin + '/login?returnTo=https%3A%2F%2Fevil.invalid' })
    await waitFor(() => evaluate("!!document.querySelector('#login-email')"))
    await field('#login-email', 'admin@etl.test'); await field('#login-password', process.env.ETL_AUTH_TEST_PASSWORD)
    await evaluate("document.querySelector('form').requestSubmit()")
    await waitFor(() => evaluate("location.pathname === '/overview/summary'"))
    await call('Page.navigate', { url: origin + '/login?returnTo=%2Fdocs' })
    await waitFor(() => evaluate("location.pathname === '/docs' && document.querySelectorAll('.opblock').length >= 47"))
    assert.deepEqual(errors, [])
    await writeFile(join(output, 'evidence.json'), JSON.stringify({ status: 'PASS', browserErrors: errors,
      checks: ['anonymous401', 'forgedProxy403', 'deepLink', 'lightDark', '320px', 'compactViewport', 'reducedMotion',
        'passwordToggle', 'genericError', 'errorFocus', 'enterSubmit', 'rememberCookie', 'httpOnly', 'noBrowserStorage',
        'csrf403DoesNotLogout', 'dockerRestartSessionPersists', 'logoutRevokes', 'expiredSessionClearsUI', 'safeReturnTo', 'authenticatedSwagger'] }, null, 2))
  } finally {
    ws?.close()
    const exited = new Promise(resolve => browser.once('exit', resolve)); browser.kill()
    await Promise.race([exited, delay(5000)])
    assert.ok(resolve(profile).startsWith(resolve(tmpdir()) + sep + 'etl-auth-browser-'))
    await rm(profile, { recursive: true, force: true, maxRetries: 4, retryDelay: 300 })
  }
})

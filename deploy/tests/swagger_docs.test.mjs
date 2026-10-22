import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import test from 'node:test'

const origin = process.env.ETL_SWAGGER_TEST_ORIGIN
const browserPath = process.env.ETL_TEST_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const skip = !origin ? 'Set ETL_SWAGGER_TEST_ORIGIN to an isolated test frontend' : false
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

test('Nginx serves local Swagger assets, OpenAPI and canonical /docs', { skip }, async () => {
  const response = await fetch(`${origin}/docs`)
  assert.equal(response.status, 200)
  assert.match(response.headers.get('content-type'), /text\/html/)
  const csp = response.headers.get('content-security-policy')
  assert.match(csp, /script-src 'self'/)
  assert.ok(!csp.includes('unsafe-eval'))
  const html = await response.text()
  assert.match(html, /<div id="swagger-ui">/)
  assert.ok(!html.includes('cdn.jsdelivr.net'))
  const trailing = await fetch(`${origin}/docs/`, { redirect: 'manual' })
  assert.equal(trailing.status, 308)
  assert.equal(new URL(trailing.headers.get('location'), origin).pathname, '/docs')
  for (const asset of ['swagger-ui.css', 'swagger-ui-bundle.js', 'swagger-init.js', 'swagger-ui-bundle.js.LICENSE.txt']) {
    const assetResponse = await fetch(`${origin}/api-docs/${asset}`)
    assert.equal(assetResponse.status, 200)
    assert.ok(!assetResponse.headers.get('content-type').includes('text/html'))
    assert.match(assetResponse.headers.get('content-security-policy'), /script-src 'self'/)
  }
  assert.equal((await fetch(`${origin}/api-docs/nonexistent.js`)).status, 404)
  const schemaResponse = await fetch(`${origin}/openapi.json`)
  assert.equal(schemaResponse.status, 200)
  const schema = await schemaResponse.json()
  assert.ok(schema.openapi.startsWith('3.'))
  assert.ok(schema.paths['/api/v1/resources'])
  assert.equal((await fetch(`${origin}/api/v1/resources`)).status, 403)
})

test('Edge renders Swagger operations under CSP without CDN requests', {
  skip: skip || (!existsSync(browserPath) ? 'Edge not installed' : false), timeout: 45000,
}, async () => {
  const profile = await mkdtemp(join(tmpdir(), 'etl-swagger-docs-'))
  const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--disable-extensions',
    '--disable-gpu', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'],
  { windowsHide: true, stdio: 'ignore' })
  let ws, nextId = 0
  const pending = new Map(), requested = [], browserErrors = []
  async function waitFor(fn) {
    for (let i = 0; i < 150; i++) {
      const value = await fn()
      if (value) return value
      await delay(100)
    }
    throw new Error('Swagger UI did not become ready')
  }
  function call(method, params = {}) {
    const id = ++nextId
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)) }, 10000)
      pending.set(id, { resolve: value => { clearTimeout(timeout); resolve(value) }, reject })
      ws.send(JSON.stringify({ id, method, params }))
    })
  }
  async function evaluate(expression) {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true })
    assert.equal(result.exceptionDetails, undefined)
    return result.result.value
  }
  try {
    const port = await waitFor(async () => {
      try { return Number((await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]) }
      catch { return null }
    })
    const pages = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
    ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl)
    ws.addEventListener('message', event => {
      const message = JSON.parse(event.data), item = pending.get(message.id)
      if (item) {
        pending.delete(message.id)
        if (message.error) item.reject(new Error(message.error.message))
        else item.resolve(message.result)
      }
      if (message.method === 'Network.requestWillBeSent') requested.push(message.params.request.url)
      if (message.method === 'Runtime.exceptionThrown') browserErrors.push(message.params.exceptionDetails.text)
      if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') browserErrors.push(message.params.entry.text)
    })
    await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }))
    await call('Page.enable')
    await call('Network.enable')
    await call('Runtime.enable')
    await call('Log.enable')
    await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false })
    await call('Page.navigate', { url: `${origin}/docs` })
    await waitFor(() => evaluate("document.querySelectorAll('.opblock').length > 0"))
    const schema = await (await fetch(`${origin}/openapi.json`)).json()
    const expected = Object.values(schema.paths).reduce((count, path) => count +
      Object.keys(path).filter(method => ['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'].includes(method)).length, 0)
    assert.equal(await evaluate("document.querySelectorAll('.opblock').length"), expected)
    assert.ok(requested.some(url => url === `${origin}/openapi.json`))
    assert.deepEqual(requested.filter(url => /^https?:/.test(url) && !url.startsWith(origin + '/')), [])
    assert.deepEqual(browserErrors, [])
    const output = resolve('.test-tmp/docker-portable/swagger-docs')
    await mkdir(output, { recursive: true })
    const screenshot = await call('Page.captureScreenshot', { format: 'png' })
    await writeFile(join(output, 'swagger-ui.png'), Buffer.from(screenshot.data, 'base64'))
    await writeFile(join(output, 'browser-evidence.json'), JSON.stringify({ operations: expected, requests: requested, browserErrors }, null, 2))
  } finally {
    for (const item of pending.values()) item.reject(new Error('Browser test finished'))
    ws?.close()
    const exited = new Promise(resolve => browser.once('exit', resolve))
    browser.kill()
    await Promise.race([exited, delay(5000)])
    assert.ok(resolve(profile).startsWith(resolve(tmpdir()) + sep + 'etl-swagger-docs-'))
    await rm(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 250 })
  }
})

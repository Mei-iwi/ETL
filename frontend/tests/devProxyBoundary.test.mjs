import assert from 'node:assert/strict'
import test from 'node:test'
import { localProxyGuard } from '../devProxyBoundary.ts'
import { loadConfigFromFile } from 'vite'
import { mkdtempSync, writeFileSync, rmSync, rmdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, relative } from 'node:path'

for (const [peer, expected] of [['127.0.0.1', true], ['::1', true], ['::ffff:127.0.0.1', true],
    ['198.51.100.2', false], ['10.1.2.3', false], ['', false]]) {
    test('Vite API proxy uses real socket peer: ' + peer, () => {
        let next = false, status = 0
        localProxyGuard({ url: '/api/v1/resources', socket: { remoteAddress: peer },
            headers: { 'x-admin': 'true', 'x-forwarded-for': '127.0.0.1' } },
            { writeHead(code) { status = code }, end() {} }, () => { next = true })
        assert.equal(next, expected)
        assert.equal(status, expected ? 0 : 403)
    })
}

test('Vite dev AND preview install the same real-peer boundary before proxying', async () => {
    const loaded = await loadConfigFromFile({ command: 'serve', mode: 'test' }, 'frontend/vite.config.ts')
    const plugin = loaded.config.plugins.find(item => item?.name === 'local-api-proxy-boundary')
    assert.ok(plugin)
    const guards = []
    for (const hook of ['configureServer', 'configurePreviewServer']) {
        const installed = []
        plugin[hook]({ middlewares: { use(fn) { installed.push(fn) } } })
        assert.equal(installed.length, 1)
        guards.push(installed[0])
        let denied = 0
        installed[0]({ url: '/admin/sync/master', socket: { remoteAddress: '198.51.100.2' } },
            { writeHead(code) { denied = code }, end() {} }, () => assert.fail('remote proxy allowed'))
        assert.equal(denied, 403)
    }
    // Vite bundles the config, so compare its two hooks, not a separately imported function.
    assert.equal(guards[0], guards[1])
})

test('local auth proxy preserves browser host and overwrites client proxy credentials server-side', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'etl-vite-auth-'))
    const originalMode = process.env.AUTH_MODE
    const originalPath = process.env.AUTH_PROXY_KEY_FILE
    const originalInline = process.env.AUTH_PROXY_KEY
    try {
        process.env.AUTH_PROXY_KEY = '' // Exercise file fallback independently of the operator's .env.
        const key = 'a'.repeat(64)
        const path = join(directory, 'proxy-key')
        writeFileSync(path, key)
        process.env.AUTH_MODE = 'single_admin'
        process.env.AUTH_PROXY_KEY_FILE = path
        const loaded = await loadConfigFromFile({ command: 'serve', mode: 'test' }, 'frontend/vite.config.ts')
        assert.equal(loaded.config.server.strictPort, true)
        for (const route of ['/api', '/admin', '/docs', '/openapi.json']) {
            const config = loaded.config.server.proxy[route]
            assert.equal(config.changeOrigin, false)
            let handler
            config.configure({ on(event, callback) {
                assert.equal(event, 'proxyReq')
                handler = callback
            } })
            const headers = { 'x-etl-proxy-key': 'untrusted-browser-value' }
            handler({ setHeader(name, value) { headers[name] = value } })
            assert.equal(headers['x-etl-proxy-key'], key)
        }
    } finally {
        if (originalMode === undefined) delete process.env.AUTH_MODE
        else process.env.AUTH_MODE = originalMode
        if (originalPath === undefined) delete process.env.AUTH_PROXY_KEY_FILE
        else process.env.AUTH_PROXY_KEY_FILE = originalPath
        if (originalInline === undefined) delete process.env.AUTH_PROXY_KEY
        else process.env.AUTH_PROXY_KEY = originalInline
        rmSync(join(directory, 'proxy-key'), { force: true })
        rmdirSync(directory)
    }
})

test('auth proxy resolves project-relative secrets when Vite runs from frontend', async () => {
    const root = process.cwd()
    const directory = mkdtempSync(join(tmpdir(), 'etl-vite-relative-auth-'))
    const originalMode = process.env.AUTH_MODE
    const originalPath = process.env.AUTH_PROXY_KEY_FILE
    const originalInline = process.env.AUTH_PROXY_KEY
    try {
        process.env.AUTH_PROXY_KEY = ''
        const keyPath = join(directory, 'proxy-key')
        writeFileSync(keyPath, 'b'.repeat(64))
        process.env.AUTH_MODE = 'master_users'
        process.env.AUTH_PROXY_KEY_FILE = relative(root, keyPath)
        const loaded = await loadConfigFromFile({ command: 'serve', mode: 'test' }, 'frontend/vite.config.ts')
        process.chdir(join(root, 'frontend'))
        const handlers = {}
        loaded.config.server.proxy['/api'].configure({ on(name, handler) { handlers[name] = handler } })
        const headers = {}
        handlers.proxyReq({ setHeader(name, value) { headers[name] = value } })
        assert.equal(headers['x-etl-proxy-key'], 'b'.repeat(64))
    } finally {
        process.chdir(root)
        if (originalMode === undefined) delete process.env.AUTH_MODE
        else process.env.AUTH_MODE = originalMode
        if (originalPath === undefined) delete process.env.AUTH_PROXY_KEY_FILE
        else process.env.AUTH_PROXY_KEY_FILE = originalPath
        if (originalInline === undefined) delete process.env.AUTH_PROXY_KEY
        else process.env.AUTH_PROXY_KEY = originalInline
        rmSync(directory, { recursive: true, force: true })
    }
})

test('inline auth proxy key stays server-side and does not need a file', async () => {
    const before = { mode: process.env.AUTH_MODE, key: process.env.AUTH_PROXY_KEY, file: process.env.AUTH_PROXY_KEY_FILE }
    try {
        process.env.AUTH_MODE = 'master_users'
        process.env.AUTH_PROXY_KEY = 'c'.repeat(64)
        process.env.AUTH_PROXY_KEY_FILE = '/missing/unused-secret'
        const loaded = await loadConfigFromFile({ command: 'serve', mode: 'test' }, 'frontend/vite.config.ts')
        let handler
        loaded.config.server.proxy['/api'].configure({ on(event, callback) { handler = callback } })
        const headers = {}
        handler({ setHeader(name, value) { headers[name] = value } })
        assert.equal(headers['x-etl-proxy-key'], 'c'.repeat(64))
        assert.equal(JSON.stringify(loaded.config.define ?? {}).includes('c'.repeat(64)), false)
        process.env.AUTH_PROXY_KEY = 'invalid-test-key'
        const invalid = await loadConfigFromFile({ command: 'serve', mode: 'test' }, 'frontend/vite.config.ts')
        assert.throws(() => invalid.config.server.proxy['/api'].configure({ on() {} }), /invalid format/)
    } finally {
        for (const [key, value] of [['AUTH_MODE', before.mode], ['AUTH_PROXY_KEY', before.key], ['AUTH_PROXY_KEY_FILE', before.file]]) {
            if (value === undefined) delete process.env[key]
            else process.env[key] = value
        }
    }
})

for (const path of ['/docs', '/docs/', '/docs?view=1', '/docs/oauth2-redirect', '/openapi.json', '/openapi.json?refresh=1']) {
    test('Swagger proxy retains loopback boundary: ' + path, () => {
        for (const [peer, expected] of [['127.0.0.1', true], ['10.1.2.3', false]]) {
            let next = false, status = 0
            localProxyGuard({ url: path, socket: { remoteAddress: peer } },
                { writeHead(code) { status = code }, end() {} }, () => { next = true })
            assert.equal(next, expected)
            assert.equal(status, expected ? 0 : 403)
        }
    })
}

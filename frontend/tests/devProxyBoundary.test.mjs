import assert from 'node:assert/strict'
import test from 'node:test'
import { localProxyGuard } from '../devProxyBoundary.ts'
import { loadConfigFromFile } from 'vite'
import { mkdtempSync, writeFileSync, rmSync, rmdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

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
    try {
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
        rmSync(join(directory, 'proxy-key'), { force: true })
        rmdirSync(directory)
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

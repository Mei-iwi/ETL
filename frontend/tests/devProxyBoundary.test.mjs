import assert from 'node:assert/strict'
import test from 'node:test'
import { localProxyGuard } from '../devProxyBoundary.ts'
import { loadConfigFromFile } from 'vite'

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

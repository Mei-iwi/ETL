import assert from 'node:assert/strict'
import test from 'node:test'
import { localProxyGuard } from '../devProxyBoundary.ts'

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

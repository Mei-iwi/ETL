import assert from 'node:assert/strict'
import test from 'node:test'
import { startIndex, getChunks, getIndexStatus, reindex, IndexingApiError } from '../src/services/indexingApi.ts'

test('summary status is opt-in and does not change the default details request', async t => {
    const paths = []
    t.mock.method(globalThis, 'fetch', async (path, options) => {
        paths.push(path)
        assert.equal(options.method, 'GET')
        return Response.json({ bags: [] })
    })
    await getIndexStatus('v1', undefined, false)
    await getIndexStatus('v1')
    assert.deepEqual(paths, ['/api/v1/resource-versions/v1/index-status?include_bags=false',
        '/api/v1/resource-versions/v1/index-status'])
})

test('start index is explicit POST with encoded version and abort signal', async () => {
    const old = globalThis.fetch
    const controller = new AbortController()
    try {
        globalThis.fetch = async (path, init) => {
            assert.equal(path, '/api/v1/resource-versions/a%2Fb/index')
            assert.equal(init.method, 'POST')
            assert.equal(init.signal.aborted, false)
            controller.abort()
            assert.equal(init.signal.aborted, true)
            return Response.json({ action: 'QUEUED', index_run_id: 'run-1' }, { status: 202 })
        }
        assert.equal((await startIndex('a/b', controller.signal)).index_run_id, 'run-1')
    } finally { globalThis.fetch = old }
})

test('structured runtime failure is shown without raw backend payload', async () => {
    const old = globalThis.fetch
    try {
        globalThis.fetch = async () => Response.json({ detail: {
            code: 'BLOCKED_EMBEDDING_RUNTIME', message: 'Chưa có model local.', phase: 'indexing',
        } }, { status: 503 })
        await assert.rejects(startIndex('version'), error => error instanceof IndexingApiError &&
            error.status === 503 && error.code === 'BLOCKED_EMBEDDING_RUNTIME' && error.message === 'Chưa có model local.')
    } finally { globalThis.fetch = old }
})

test('chunks pagination and unit filter use GET with encoded parameters', async () => {
    const old = globalThis.fetch
    try {
        globalThis.fetch = async (path, init) => {
            assert.equal(path, '/api/v1/resource-versions/v%2F1/chunks?offset=10&limit=10&content_unit_id=u%2F1')
            assert.equal(init.method, 'GET')
            return Response.json({ items: [], total: 0 })
        }
        assert.equal((await getChunks('v/1', 10, undefined, 'u/1')).total, 0)
    } finally { globalThis.fetch = old }
})

test('status is read-only and reindex only sends an explicit POST', async () => {
    const old = globalThis.fetch
    const calls = []
    try {
        globalThis.fetch = async (path, init) => {
            calls.push([path, init.method])
            return Response.json({ index_status: 'PENDING' })
        }
        await getIndexStatus('v/1')
        await reindex('v/1')
        assert.deepEqual(calls, [['/api/v1/resource-versions/v%2F1/index-status', 'GET'],
            ['/api/v1/resource-versions/v%2F1/reindex', 'POST']])
    } finally { globalThis.fetch = old }
})

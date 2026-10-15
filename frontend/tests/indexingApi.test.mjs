import assert from 'node:assert/strict'
import test from 'node:test'
import { startIndex, getChunks, IndexingApiError } from '../src/services/indexingApi.ts'

test('start index is explicit POST with encoded version and abort signal', async () => {
    const old = globalThis.fetch
    const controller = new AbortController()
    try {
        globalThis.fetch = async (path, init) => {
            assert.equal(path, '/api/v1/resource-versions/a%2Fb/index')
            assert.equal(init.method, 'POST')
            assert.equal(init.signal, controller.signal)
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

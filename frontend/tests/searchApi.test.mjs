import assert from 'node:assert/strict'
import test from 'node:test'
import { SearchApiError, getChunkContext, searchMaterials } from '../src/services/searchApi.ts'
import { readSearch, writeSearch } from '../src/pages/search/searchParams.ts'
import { searchResponse, contextResponse } from './helpers/searchResponses.mjs'

test('search sends exactly one POST with JSON criteria and encoded URL state', async () => {
    const request = { query: 'định luật', method: 'hybrid', filters: { subject_id: 's/1' }, page: 2, size: 10 }
    const old = globalThis.fetch
    let calls = 0
    try {
        globalThis.fetch = async (path, init) => {
            calls++
            assert.equal(path, '/api/v1/search')
            assert.equal(init.method, 'POST')
            assert.equal(init.headers['Content-Type'], 'application/json')
            assert.deepEqual(JSON.parse(init.body), request)
            return Response.json({ ...searchResponse(request), items: [] })
        }
        assert.equal((await searchMaterials(request)).total, 1)
        assert.equal(calls, 1)
        const restored = readSearch(writeSearch(request))
        assert.equal(restored.query, request.query)
        assert.equal(restored.filters.subject_id, 's/1')
        assert.equal(restored.page, 2)
    } finally { globalThis.fetch = old }
})

for (const status of [401, 403, 422, 503]) test(`structured ${status} is not silently downgraded to BoW`, async () => {
    const old = globalThis.fetch
    try {
        globalThis.fetch = async () => Response.json({ detail: { code: 'TEST_ERROR', message: 'Không thể tìm.' } }, { status })
        await assert.rejects(searchMaterials({ query: 'x', method: 'vector', filters: {}, page: 1, size: 10 }),
            error => error instanceof SearchApiError && error.status === status && error.code === 'TEST_ERROR')
    } finally { globalThis.fetch = old }
})

test('client never injects credentials or a self-declared internal scope', async () => {
    const old = globalThis.fetch
    try {
        globalThis.fetch = async (path, init) => {
            assert.equal(init.headers.Authorization, undefined)
            assert.equal(init.headers['X-Internal'], undefined)
            assert.equal(JSON.parse(init.body).internal, undefined)
            return Response.json({ detail: 'Authentication required' }, { status: 401 })
        }
        await assert.rejects(searchMaterials({ query: 'x', method: 'bow', filters: {}, page: 1, size: 10 }),
            error => error.status === 401 && error.message.includes('Yêu cầu xác thực'))
    } finally { globalThis.fetch = old }
})

test('cancellation is forwarded; a timeout signal is always supplied', async () => {
    const old = globalThis.fetch
    const controller = new AbortController()
    try {
        globalThis.fetch = async (path, init) => {
            assert.ok(init.signal instanceof AbortSignal)
            controller.abort()
            init.signal.throwIfAborted()
        }
        await assert.rejects(searchMaterials({ query: 'x', method: 'bow', filters: {}, page: 1, size: 10 }, controller.signal),
            error => error.name === 'AbortError')
    } finally { globalThis.fetch = old }
})

test('URL defaults and bounds are safe and methods are never arbitrary', () => {
    const request = readSearch(new URLSearchParams('q=%20abc%20&method=bad&page=-1&size=500'))
    assert.equal(request.query, 'abc')
    assert.equal(request.method, 'hybrid')
    assert.equal(request.page, 1)
    assert.equal(request.size, 5)
    assert.equal(readSearch(new URLSearchParams('size=50')).size, 5)
})

test('context uses the search locator, encoded GET and bounded neighbors', async () => {
    const old = globalThis.fetch
    try {
        globalThis.fetch = async (path, init) => {
            assert.equal(path, '/api/v1/chunks/a%2Fb/context?neighbors=2')
            assert.equal(init.method, 'GET')
            assert.equal(init.body, undefined)
            return Response.json({ ...contextResponse(), context_id: 'a/b', neighbors: 2 })
        }
        assert.equal((await getChunkContext('a/b', undefined, 2)).chunks[0].text, 'Nguồn thật')
    } finally { globalThis.fetch = old }
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { SearchApiError, getChunkContext, searchMaterials } from '../src/services/searchApi.ts'
import { readSearch, writeSearch } from '../src/pages/search/searchParams.ts'
import { searchResponse, contextResponse } from './helpers/searchResponses.mjs'
import { normalizeSearchQuery } from '../src/services/searchQuery.ts'
import { SEARCH_QUERY_MAX_LENGTH } from '../src/pages/search/searchParams.ts'

test('query normalization and additive planner diagnostics match the API contract', async t => {
    const query = '  C++\t và\n C# CO₂  '
    const normalized = normalizeSearchQuery(query)
    assert.equal(normalized, 'C++ và C# CO₂')
    assert.equal(SEARCH_QUERY_MAX_LENGTH, 4000)
    const request = { query, method: 'bow', filters: {}, page: 1, size: 10 }
    const body = { ...searchResponse(request), query: normalized, query_plan: { canonical_query: normalized },
        retrieval_diagnostics: { semantic_queries: [], fusion: 'per-bag-max-before-rrf' } }
    t.mock.method(globalThis, 'fetch', async () => Response.json(body))
    const result = await searchMaterials(request)
    assert.equal(result.query, normalized)
    assert.equal(readSearch(writeSearch(request)).query, normalized)
})

test('lexical evidence keeps real source terms and rejects malformed explanations', async () => {
    const old = globalThis.fetch
    const request = { query: 'học máy', method: 'bow', filters: { domain: 'computing' }, page: 1, size: 10 }
    const evidence = { query_phrase: 'học máy', matched_phrase: 'Machine learning', concept_id: 'etl:machine_learning',
        relation: 'translation', field: 'content', weight: 0.65, sources: [{ name: 'curated', identifier: 'v1' }] }
    try {
        for (const rows of [undefined, [], [evidence], null, [{}], [{ ...evidence, matched_phrase: {} }], [{ ...evidence, field: 'invented' }]]) {
            const body = searchResponse(request)
            body.items[0].lexical_evidence = rows
            globalThis.fetch = async () => Response.json(body)
            if (rows === undefined || (Array.isArray(rows) && (rows.length === 0 || rows[0] === evidence))) {
                assert.deepEqual((await searchMaterials(request)).items[0].lexical_evidence, rows)
            } else await assert.rejects(searchMaterials(request), error => error.code === 'SEARCH_INVALID_RESPONSE')
        }
        assert.equal(readSearch(writeSearch(request)).filters.domain, 'computing')
    } finally { globalThis.fetch = old }
})

for (const code of ['BLOCKED_INDEX_SCHEMA', 'SEARCH_SCHEMA_UNAVAILABLE']) {
    test('search schema errors display safe actionable text: ' + code, async () => {
        const old = globalThis.fetch
        try {
            globalThis.fetch = async () => Response.json({ detail: { code, message: 'PRIVATE_INTERNAL' } }, { status: 503 })
            await assert.rejects(searchMaterials({ query: 'x', method: 'bow', filters: {}, page: 1, size: 10 }),
                error => error instanceof SearchApiError && error.status === 503 && error.code === code
                    && error.message.includes('chỉ mục') && !error.message.includes('PRIVATE_INTERNAL')
                    && !error.message.includes('HTTP 503'))
        } finally { globalThis.fetch = old }
    })
}

test('optional corpus readiness accepts old responses and rejects malformed counts', async () => {
    const old = globalThis.fetch
    const request = { query: 'x', method: 'bow', filters: {}, page: 1, size: 10 }
    try {
        const readiness = { lexical_ready_bags: 2, dense_ready_bags: 2, eligible_versions: 3, ready_versions: 2, reindex_required: true }
        globalThis.fetch = async () => Response.json({ ...searchResponse(request), readiness })
        assert.deepEqual((await searchMaterials(request)).readiness, readiness)
        globalThis.fetch = async () => Response.json({ ...searchResponse(request), readiness: { ...readiness, ready_versions: 4 } })
        await assert.rejects(searchMaterials(request), error => error.code === 'SEARCH_INVALID_RESPONSE')
    } finally { globalThis.fetch = old }
})

test('context evidence accepts legacy results and validates passage provenance', async () => {
    const old = globalThis.fetch
    const request = { query: 'x', method: 'bow', filters: {}, page: 1, size: 10 }
    try {
        for (const evidence of [undefined, null, 'lexical', 'semantic', 'bag', 'invented']) {
            const body = searchResponse(request)
            body.items[0].context_evidence = evidence
            globalThis.fetch = async () => Response.json(body)
            if (evidence === 'invented') await assert.rejects(searchMaterials(request), error => error.code === 'SEARCH_INVALID_RESPONSE')
            else assert.equal((await searchMaterials(request)).items[0].context_evidence, evidence)
        }
    } finally { globalThis.fetch = old }
})

test('observed meaning variants must be an array of source strings', async () => {
    const old = globalThis.fetch
    const request = { query: 'x', method: 'vector', filters: {}, page: 1, size: 10 }
    try {
        for (const variants of [undefined, [], ['học máy', 'machine learning'], [1], 'wrong', null]) {
            const body = searchResponse(request)
            body.items[0].meaning_variants = variants
            globalThis.fetch = async () => Response.json(body)
            if (variants === null || typeof variants === 'string' || variants?.[0] === 1) {
                await assert.rejects(searchMaterials(request), error => error.code === 'SEARCH_INVALID_RESPONSE')
            } else assert.deepEqual((await searchMaterials(request)).items[0].meaning_variants, variants)
        }
    } finally { globalThis.fetch = old }
})

test('search sends exactly one POST with JSON criteria and encoded URL state', async () => {
    const request = { query: 'định luật', method: 'hybrid', filters: { subject_id: 's/1' }, page: 2, size: 10 }
    const old = globalThis.fetch
    let calls = 0
    try {
        globalThis.fetch = async (path, init) => {
            calls++
            assert.equal(path, '/api/v1/search')
            assert.equal(init.method, 'POST')
            assert.equal(init.cache, 'no-store')
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
            assert.equal(init.cache, 'no-store')
            assert.equal(init.body, undefined)
            return Response.json({ ...contextResponse(), context_id: 'a/b', neighbors: 2 })
        }
        assert.equal((await getChunkContext('a/b', undefined, 2)).chunks[0].text, 'Nguồn thật')
    } finally { globalThis.fetch = old }
})

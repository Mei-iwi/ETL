import assert from 'node:assert/strict'
import test from 'node:test'
import { SearchApiError, getChunkContext, searchMaterials } from '../src/services/searchApi.ts'
import { searchRequest, searchResponse, contextResponse } from './helpers/searchResponses.mjs'

const malformed = [null, [], {}, { detail: { message: 'PRIVATE_INTERNAL' } },
    { ...searchResponse(), items: {} }, { ...searchResponse(), total: '1' },
    { ...searchResponse(), items: [null] }, { ...searchResponse(), score: .5, items: [{ resource_id: 'r1' }] },
    { ...searchResponse(), items: [{ ...searchResponse().items[0], score: '0.5' }] },
    { ...searchResponse(), items: [{ ...searchResponse().items[0], matched_keywords: [1] }] },
    { ...searchResponse(), items: [{ ...searchResponse().items[0], score_components: [] }] },
    { ...searchResponse(), query: 'wrong request' }, { ...searchResponse(), method: 'bow' },
    { ...searchResponse(), page: 2 }, { ...searchResponse(), size: 10 },
]
for (const [index, payload] of malformed.entries()) test(`malformed Search HTTP200 case ${index} rejects`, async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async () => Response.json(payload)
        await assert.rejects(searchMaterials(searchRequest), error => error instanceof SearchApiError && error.code === 'SEARCH_INVALID_RESPONSE' && !error.message.includes('PRIVATE_INTERNAL'))
    } finally { globalThis.fetch = original }
})
test('malformed JSON HTTP200 rejects, valid additive contract and retry succeed', async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async () => new Response('{broken', { status: 200 })
        await assert.rejects(searchMaterials(searchRequest), error => error.code === 'SEARCH_INVALID_RESPONSE')
        const payload = { ...searchResponse(), future_timing: { elapsed_ms: 5 } }
        globalThis.fetch = async () => Response.json(payload)
        assert.deepEqual(await searchMaterials(searchRequest), payload)
    } finally { globalThis.fetch = original }
})
for (const payload of [null, [], { chunks: [] }, { ...contextResponse(), chunks: [null] },
    { ...contextResponse(), heading_path: [null] }, { ...contextResponse(), chunks: [{ ...contextResponse().chunks[0], selected: 'true' }] }]) {
    test(`invalid Context HTTP200 ${JSON.stringify(payload)} rejects`, async () => {
        const original = globalThis.fetch
        try {
            globalThis.fetch = async () => Response.json(payload)
            await assert.rejects(getChunkContext('token'), error => error.code === 'SEARCH_INVALID_RESPONSE')
        } finally { globalThis.fetch = original }
    })
}
for (const status of [401, 403, 404, 409, 422, 502, 503]) test(`safe API error ${status}`, async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async () => Response.json({ detail: { code: 'PRIVATE_ERROR', message: 'PRIVATE_INTERNAL' } }, { status })
        await assert.rejects(searchMaterials(searchRequest), error => error.status === status && !error.message.includes('PRIVATE_INTERNAL'))
    } finally { globalThis.fetch = original }
})
test('network failure and real deadline abort have safe messages', async () => {
    const original = globalThis.fetch, timeout = AbortSignal.timeout
    try {
        globalThis.fetch = async () => { throw new TypeError('PRIVATE_INTERNAL') }
        await assert.rejects(searchMaterials(searchRequest), error => error.code === 'SEARCH_NETWORK_ERROR' && !error.message.includes('PRIVATE_INTERNAL'))
        AbortSignal.timeout = () => timeout(5)
        globalThis.fetch = async (_, { signal }) => new Promise((resolve, reject) => {
            const keepAlive = setTimeout(() => reject(new Error('test deadline missing')), 1000)
            signal.addEventListener('abort', () => { clearTimeout(keepAlive); reject(signal.reason) }, { once: true })
        })
        await assert.rejects(searchMaterials(searchRequest), error => error.code === 'SEARCH_TIMEOUT')
    } finally { globalThis.fetch = original; AbortSignal.timeout = timeout }
})
test('explicit cancellation remains AbortError, not network/API error', async () => {
    const original = globalThis.fetch
    try {
        const controller = new AbortController()
        globalThis.fetch = async (_, { signal }) => { controller.abort(); signal.throwIfAborted() }
        await assert.rejects(searchMaterials(searchRequest, controller.signal), error => error.name === 'AbortError')
    } finally { globalThis.fetch = original }
})
test('cancellation during body read cannot publish an already aborted success', async () => {
    const original = globalThis.fetch
    try {
        const controller = new AbortController()
        globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => {
            controller.abort()
            return searchResponse()
        } })
        await assert.rejects(searchMaterials(searchRequest, controller.signal), error => error.name === 'AbortError')
    } finally { globalThis.fetch = original }
})
test('body stream disconnect is a network error, not malformed JSON', async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => { throw new TypeError('PRIVATE_INTERNAL') } })
        await assert.rejects(searchMaterials(searchRequest), error => error.code === 'SEARCH_NETWORK_ERROR' && !error.message.includes('PRIVATE_INTERNAL'))
    } finally { globalThis.fetch = original }
})

for (const phase of ['headers', 'body']) test(`BoW deadline settles even when ${phase} transport never settles`, async () => {
    const original = globalThis.fetch, timeout = AbortSignal.timeout
    const keepAlive = setTimeout(() => {}, 1000)
    try {
        let budget
        AbortSignal.timeout = ms => { budget = ms; return timeout(5) }
        const hanging = new Promise(() => {})
        globalThis.fetch = phase === 'headers' ? () => hanging
            : async () => ({ ok: true, status: 200, json: () => hanging })
        await assert.rejects(searchMaterials({ ...searchRequest, method: 'bow' }),
            error => error.code === 'SEARCH_TIMEOUT')
        assert.equal(budget, 20000)
        globalThis.fetch = async () => Response.json(searchResponse({ ...searchRequest, method: 'bow' }))
        assert.equal((await searchMaterials({ ...searchRequest, method: 'bow' })).method, 'bow')
    } finally { clearTimeout(keepAlive); globalThis.fetch = original; AbortSignal.timeout = timeout }
})

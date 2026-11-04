import assert from 'node:assert/strict'
import test from 'node:test'
import { loadRelatedLearning } from '../src/services/relatedLearningApi.ts'

const item = { resource_id: 'other', title: 'Other', reason_label: 'Cùng môn học', url: '/library/other' }

test('related request uses cookie auth, no store, abort and exact route', async () => {
    const old = globalThis.fetch
    const controller = new AbortController()
    try {
        globalThis.fetch = async (url, options) => {
            assert.equal(url, '/api/v1/resources/seed/related-learning')
            assert.equal(options.credentials, 'same-origin')
            assert.equal(options.cache, 'no-store')
            assert.equal(options.signal, controller.signal)
            return Response.json({ items: [item] })
        }
        assert.deepEqual(await loadRelatedLearning('seed', controller.signal), [item])
    } finally { globalThis.fetch = old }
})

test('empty, unavailable, malformed or unsafe suggestions disappear without affecting detail', async () => {
    const old = globalThis.fetch
    try {
        for (const items of [[], [item, item], [{ ...item, url: 'https://example.test' }],
            [{ ...item, resource_id: 'seed' }], [{ ...item, reason_label: 'internal ID' }],
            [item, item, item, item], [{ ...item, title: '' }]]) {
            globalThis.fetch = async () => Response.json({ items })
            assert.deepEqual(await loadRelatedLearning('seed'), [])
        }
        globalThis.fetch = async () => new Response(null, { status: 503 })
        assert.deepEqual(await loadRelatedLearning('seed'), [])
        globalThis.fetch = async () => { throw new Error('offline') }
        assert.deepEqual(await loadRelatedLearning('seed'), [])
    } finally { globalThis.fetch = old }
})

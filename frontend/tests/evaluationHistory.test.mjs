import assert from 'node:assert/strict'
import test from 'node:test'
import { listEvaluationRuns } from '../src/services/evaluationApi.ts'

const id = '01M4ZXS1XJ2B4H42CFY24KJY8C'
const fixture = { page: 2, size: 7, total: 8, total_pages: 2, items: [
    { id, dataset_id: id, dataset_name: 'Thực nghiệm', created_at: '2026-10-18T10:00:00Z',
        finished_at: null, duration_ms: 1200, status: 'COMPLETED', query_count: 18 },
] }

test('history loads server page without requiring an ID or leaking credentials', async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async (url, init) => {
            assert.equal(url, '/api/v1/evaluations/runs?page=2&size=7')
            assert.equal(init.method, 'GET')
            assert.equal(init.headers.Authorization, undefined)
            assert.ok(init.signal instanceof AbortSignal)
            return Response.json(fixture)
        }
        assert.deepEqual(await listEvaluationRuns(2), fixture)
    } finally { globalThis.fetch = original }
})

test('malformed history and API failure reject rather than leaving loading pending', async () => {
    const original = globalThis.fetch
    try {
        for (const data of [null, [], {}, { ...fixture, page: 1 },
            { ...fixture, items: [null] }, { ...fixture, items: [{ ...fixture.items[0], duration_ms: -1 }] },
            { ...fixture, items: [{ ...fixture.items[0], created_at: 'bad' }] }]) {
            globalThis.fetch = async () => Response.json(data)
            await assert.rejects(listEvaluationRuns(2))
        }
        globalThis.fetch = async () => new Response('', { status: 403 })
        await assert.rejects(listEvaluationRuns(2), error => error.status === 403)
        globalThis.fetch = async () => { throw new DOMException('Cancelled', 'AbortError') }
        await assert.rejects(listEvaluationRuns(2), error => error.name === 'AbortError')
    } finally { globalThis.fetch = original }
})

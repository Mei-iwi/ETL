import assert from 'node:assert/strict'
import test from 'node:test'
import { createDataset, runEvaluation, getEvaluationRun, getEvaluationReport, EvaluationApiError } from '../src/services/evaluationApi.ts'
import { datasetInput, importDraft, number } from '../src/pages/experiments/evaluationHelpers.ts'

const id = '01M4ZXS1XJ2B4H42CFY24KJY8C'
test('manual labels must be complete, unique and human-confirmed', () => {
    const rows = [{ query_id: 'q1', query: '  học liệu  ', labels: 'resource-1:3\nresource-2:0' }]
    assert.throws(() => datasetInput('name', '', rows, false))
    const result = datasetInput('name', '', rows, true)
    assert.equal(result.relevance_unit, 'resource')
    assert.equal(result.queries[0].query, 'học liệu')
    assert.equal(result.queries[0].relevance[1].grade, 0)
    for (const labels of ['resource-1:', 'resource-1:4', 'resource-1:1,resource-1:0']) {
        assert.throws(() => datasetInput('name', '', [{ ...rows[0], labels }], true))
    }
    assert.equal(number(null), 'n/a')
    assert.equal(number(0), '0.0000')
})

test('annotation import never invents null grades or Ground Truth', () => {
    const draft = importDraft({ rows: [{ query_id: 'q1', query_text: 'học', resource_id: 'r1', relevance: null }] })
    assert.equal(draft.queries[0].labels, 'r1:')
    assert.throws(() => datasetInput('sample', '', draft.queries, true))
})

test('four endpoints use only the intended methods, with no secrets or invented scope', async () => {
    const original = globalThis.fetch
    const calls = []
    try {
        globalThis.fetch = async (path, init) => {
            calls.push({ path, method: init.method })
            assert.equal(init.headers.Authorization, undefined)
            assert.ok(init.signal instanceof AbortSignal)
            return path.includes('/report') ? new Response('\ufeffdataset,precision\nsample,0.2', { headers: { 'Content-Type': 'text/csv' } }) : Response.json({ dataset_id: id, run_id: id, observations: [] })
        }
        await createDataset(datasetInput('fixture', '', [{ query_id: 'q1', query: 'page', labels: 'r1:3' }], true))
        await runEvaluation({ dataset_id: id, methods: ['bow', 'vector', 'hybrid'], k_values: [1,5], max_results: 10, warmup_queries: 0 })
        await getEvaluationRun(id, 20)
        assert.equal((await getEvaluationReport(id, 'csv')).filename, `evaluation-${id}.csv`)
        assert.deepEqual(calls.map(call => call.method), ['POST', 'POST', 'GET', 'GET'])
        assert.equal(calls[2].path, `/api/v1/evaluations/runs/${id}?offset=20&limit=20`)
        await assert.rejects(getEvaluationRun('../bad'))
    } finally { globalThis.fetch = original }
})

for (const status of [401,403,404,409,422,503]) test(`evaluation handles HTTP ${status}`, async () => {
    const original = globalThis.fetch
    try {
        globalThis.fetch = async () => Response.json({ detail: { code: 'SAFE_CODE', message: 'Lỗi thực nghiệm.' } }, { status })
        await assert.rejects(getEvaluationRun(id), error => error instanceof EvaluationApiError && error.status === status && error.code === 'SAFE_CODE')
    } finally { globalThis.fetch = original }
})

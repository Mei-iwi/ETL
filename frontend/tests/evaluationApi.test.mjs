import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import { createDataset, runEvaluation, getEvaluationRun, getEvaluationReport, EvaluationApiError } from '../src/services/evaluationApi.ts'
import { blankQuery, datasetInput, importDraft, setDraftGrade, number, visibleWarnings } from '../src/pages/experiments/evaluationHelpers.ts'

test('hide only the unconfirmed-label notice without changing API warning data', () => {
    const original = Object.freeze(['LABELS_NOT_HUMAN_CONFIRMED', 'EVALUATION_TIMEOUT',
        'UNJUDGED_ITEMS_TREATED_AS_GRADE_0', 'LOCAL_DEMO_NOT_PRODUCTION_AUTH'])
    assert.deepEqual(visibleWarnings(original), original.slice(1))
    assert.equal(original[0], 'LABELS_NOT_HUMAN_CONFIRMED')
    assert.deepEqual(visibleWarnings(['LABELS_NOT_HUMAN_CONFIRMED']), [])
    assert.deepEqual(visibleWarnings([]), [])
})

const id = '01M4ZXS1XJ2B4H42CFY24KJY8C'

test('run summary omits observations only on explicit request and keeps the abort signal', async t => {
    const controller = new AbortController()
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(url, `/api/v1/evaluations/runs/${id}?offset=0&limit=20&include_observations=false`)
        assert.equal(options.method, 'GET')
        controller.abort()
        assert.equal(options.signal.aborted, true)
        return Response.json({ observations: [], summary: { fixture: true } })
    })
    assert.deepEqual((await getEvaluationRun(id, 0, controller.signal, false)).observations, [])
})
test('Prompt05 draft imports 18 queries without inventing grades or Dev IDs', async () => {
    const file = JSON.parse(await readFile(new URL('../../fixtures/evaluation/manual-ground-truth-template.json', import.meta.url), 'utf8'))
    assert.equal(file.ground_truth_confirmed, false)
    assert.equal(new Set(file.rows.map(row => row.query_id)).size, 18)
    assert.ok(file.rows.every(row => row.resource_id === 'READY_RESOURCE_ID' && row.relevance === null))
    const draft = importDraft(file)
    assert.equal(draft.queries.length, 18)
    assert.throws(() => datasetInput(draft.name, draft.description, draft.queries), /chưa chấm/)
})
test('adding queries after deletion or import never reuses an existing query ID', () => {
    const rows = [blankQuery(1), blankQuery(3), blankQuery(4)]
    assert.equal(blankQuery(rows.length + 1, rows).query_id, 'q5')
    assert.equal(blankQuery(1).query_id, 'q1')
})

test('manual labels must be complete and unique without a confirmation checkbox', () => {
    const rows = [{ query_id: 'q1', query: '  học liệu  ', labels: 'resource-1:3\nresource-2:0' }]
    assert.equal(datasetInput('name', '', rows).ground_truth_confirmed, false)
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

test('annotation JSON retains descriptions and can be graded without typing IDs', () => {
    const draft = importDraft({ name: 'Test', rows: [
        { query_id: 'q01', query_text: 'học', resource_id: 'r1', resource_title: 'Học liệu 1', candidate_description: 'Nội dung', relevance: null },
        { query_id: 'q01', query_text: 'học', resource_id: 'r2', relevance: 0 },
    ] })
    assert.equal(draft.queries[0].candidates[0].title, 'Học liệu 1')
    assert.equal(draft.queries[0].candidates[0].description, 'Nội dung')
    assert.throws(() => datasetInput(draft.name, '', draft.queries, true), /còn 1 học liệu chưa chấm/)
    const graded = setDraftGrade(draft.queries[0], 'r1', 3)
    assert.deepEqual(datasetInput(draft.name, '', [graded], true).queries[0].relevance, [
        { item_id: 'r1', grade: 3 }, { item_id: 'r2', grade: 0 },
    ])
    assert.throws(() => datasetInput(draft.name, '', [setDraftGrade(graded, 'r1', null)], true), /chưa chấm/)
})

test('API-format JSON preserves complete grades; invalid or duplicate imported labels are rejected', () => {
    const input = { name: 'Test', queries: [{ query_id: 'q1', query: 'học', relevance: [{ item_id: 'r1', grade: 2 }] }] }
    const draft = importDraft(input)
    assert.deepEqual(datasetInput(draft.name, '', draft.queries, true).queries, input.queries)
    for (const grade of [4, -1, true, '3', 1.5]) {
        assert.throws(() => importDraft({ ...input, queries: [{ ...input.queries[0], relevance: [{ item_id: 'r1', grade }] }] }))
    }
    assert.throws(() => importDraft({ ...input, queries: [{ ...input.queries[0], relevance: [null] }] }))
    assert.throws(() => importDraft({ rows: [1, 2].map(() => ({ query_id: 'q1', query_text: 'học', resource_id: 'r1', relevance: 0 })) }))
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

const streamInput = { dataset_id: id, methods: ['bow', 'vector', 'hybrid'], k_values: [1], max_results: 10, warmup_queries: 0 }
test('streaming progress handles fragmented UTF-8 frames and preserves JSON compatibility', async () => {
    const original = globalThis.fetch, progress = []
    const result = { run_id: id, dataset_id: id, status: 'COMPLETED', warnings: [] }
    const frames = [
        { type: 'progress', run_id: id, completed: 0, total: 3, percent: 0, phase: 'preparing' },
        { type: 'progress', run_id: id, completed: 1, total: 3, percent: 33, phase: 'running' },
        { type: 'progress', run_id: id, completed: 3, total: 3, percent: 99, phase: 'running' },
        { type: 'result', completed: 3, total: 3, percent: 100, result },
    ]
    const bytes = new TextEncoder().encode(frames.map(frame => JSON.stringify(frame)).join('\n') + '\n')
    try {
        globalThis.fetch = async (url, init) => {
            assert.equal(url, '/api/v1/evaluations/runs')
            assert.equal(init.headers.Accept, 'application/x-ndjson')
            return new Response(new ReadableStream({ start(controller) {
                for (let i = 0; i < bytes.length; i += 11) controller.enqueue(bytes.slice(i, i + 11))
                controller.close()
            } }), { headers: { 'Content-Type': 'application/x-ndjson' } })
        }
        assert.deepEqual(await runEvaluation(streamInput, undefined, value => progress.push(value.percent)), result)
        assert.deepEqual(progress, [0, 33, 99, 100])
    } finally { globalThis.fetch = original }
})

for (const failure of ['truncated', 'error', 'false_complete', 'legacy', 'abort']) test(`streaming run rejects ${failure} without retrying POST`, async () => {
    const original = globalThis.fetch
    let calls = 0
    try {
        globalThis.fetch = async () => {
            calls++
            if (failure === 'legacy') return Response.json({ run_id: id })
            if (failure === 'abort') return new Response(new ReadableStream({ start(controller) { controller.error(new DOMException('Aborted', 'AbortError')) } }), { headers: { 'Content-Type': 'application/x-ndjson' } })
            const event = failure === 'error' ? { type: 'error', status: 503, detail: { code: 'EVALUATION_STORAGE_UNAVAILABLE', message: 'Lỗi lưu' } }
                : failure === 'false_complete' ? { type: 'result', completed: 1, total: 3, percent: 100, result: { run_id: id, status: 'FAILED' } }
                : { type: 'progress', completed: 1, total: 3, percent: 33, phase: 'running' }
            return new Response(JSON.stringify(event) + '\n', { headers: { 'Content-Type': 'application/x-ndjson' } })
        }
        await assert.rejects(runEvaluation(streamInput, undefined, () => {}))
        assert.equal(calls, 1)
    } finally { globalThis.fetch = original }
})

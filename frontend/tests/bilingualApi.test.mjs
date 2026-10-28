import assert from 'node:assert/strict'
import test from 'node:test'
import { BilingualApiError, getBilingualView } from '../src/services/bilingualApi.ts'

const revision = 'a'.repeat(64)
const payload = (offset = 0) => ({ resource_version_id: 'v1', items: [],
    summary: { total: 25, translated_count: 1, timed_count: 25, timed_translated_count: 1, revision },
    pagination: { offset, limit: 20, total: 25 } })

test('closed bilingual reads only metadata; open/page pins the revision and encodes the version', async t => {
    const calls = []
    t.mock.method(globalThis, 'fetch', async (path, options) => {
        calls.push({ path: String(path), signal: options.signal })
        return Response.json(payload(calls.length === 1 ? 0 : 20))
    })
    const controller = new AbortController()
    await getBilingualView('v1', controller.signal)
    await getBilingualView('v1', controller.signal, { includeItems: true, offset: 20, revision })
    assert.match(calls[0].path, /include_items=false&offset=0&limit=20$/)
    assert.match(calls[1].path, new RegExp(`include_items=true&offset=20&limit=20&revision=${revision}$`))
    assert.equal(calls[0].signal, controller.signal)
    controller.abort()
    assert.equal(calls[1].signal.aborted, true)
})

for (const change of [ { resource_version_id: 'other' }, { summary: { ...payload().summary, revision: 'b'.repeat(64) } },
    { pagination: { offset: 20, limit: 20, total: 25 } }, { items: Array(21).fill({}) },
    { summary: { ...payload().summary, translated_count: 26 } } ]) {
    test('invalid or stale bilingual response is rejected: ' + JSON.stringify(change).slice(0, 65), async t => {
        t.mock.method(globalThis, 'fetch', async () => Response.json({ ...payload(), ...change }))
        await assert.rejects(getBilingualView('v1', new AbortController().signal, { includeItems: true, revision }))
    })
}

for (const status of [401, 403, 409, 503]) {
    test('bilingual read preserves HTTP failure ' + status, async t => {
        t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'fixture' }, { status }))
        await assert.rejects(getBilingualView('v1', new AbortController().signal), error => error instanceof BilingualApiError && error.status === status)
    })
}

import assert from 'node:assert/strict'
import test from 'node:test'
import { catalogMessage, getProviders, getResourceTypes } from '../src/services/libraryLookupApi.ts'

const full = { source_status: 'SOURCE_OK', completeness: 'ALL_SOURCE_PLUS_LOCAL' }
const partial = { source_status: 'SOURCE_UNAVAILABLE', completeness: 'PARTIAL_FALLBACK' }

test('full SOURCE and partial fallback labels are distinct including empty fallback', () => {
    assert.match(catalogMessage(full), /Đầy đủ từ SOURCE/)
    assert.match(catalogMessage(partial), /có thể thiếu/)
    assert.match(catalogMessage(), /Chưa xác minh/)
    assert.match(catalogMessage({ ...full, source_status: 'SOURCE_EMPTY' }), /không có danh mục active/)
})

test('form catalogs retain metadata, unused identities and names; no write request', async t => {
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        assert.equal(options.method, undefined)
        return Response.json({ catalog: full, items: [{ id: 'unused', name: 'Unused', provider_type: 'INTERNAL' }], pagination: { total_pages: 1 } })
    })
    const options = await getProviders()
    assert.equal(options[0].id, 'unused')
    assert.deepEqual(options.catalog, full)
})

test('getAll detects SOURCE status change and deduplicates identities across pages', async t => {
    let count = 0
    t.mock.method(globalThis, 'fetch', async () => {
        count++
        return Response.json({ catalog: count === 1 ? full : partial, items: [{ code: 'TYPE', name: 'Name' }], pagination: { total_pages: 2 } })
    })
    await assert.rejects(getResourceTypes(), /đã thay đổi/)
    count = 0
    t.mock.method(globalThis, 'fetch', async () => Response.json({ catalog: full,
        items: [{ code: 'TYPE', name: 'Name' }], pagination: { total_pages: 2 } }))
    const options = await getResourceTypes()
    assert.equal(options.length, 1)
})

test('aborted form pagination rejects stale responses', async t => {
    const controller = new AbortController()
    t.mock.method(globalThis, 'fetch', async () => {
        controller.abort()
        return Response.json({ catalog: partial, items: [], pagination: { total_pages: 2 } })
    })
    await assert.rejects(getProviders(controller.signal), /Abort/)
})

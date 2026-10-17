import assert from 'node:assert/strict'
import test from 'node:test'
import { getCatalogPage, getProviders } from '../src/services/libraryLookupApi.ts'

test('Master catalog page forwards page/size/Unicode q without downloading all pages', async t => {
    const calls = []
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push([url, options])
        return Response.json({ items: [], pagination: { page: 4, size: 7, total: 1001, total_pages: 143 } })
    })
    const signal = new AbortController().signal
    await getCatalogPage('/api/v1/providers', 4, signal, { size: 7, query: '  Tiếng Việt  ' })
    assert.equal(calls.length, 1)
    const url = new URL(calls[0][0], 'http://127.0.0.1')
    assert.equal(url.searchParams.get('page'), '4')
    assert.equal(url.searchParams.get('size'), '7')
    assert.equal(url.searchParams.get('q'), 'Tiếng Việt')
    assert.equal(calls[0][1].signal, signal)
})
test('form getAll rejects an unbounded server page count instead of looping forever', async t => {
    t.mock.method(globalThis, 'fetch', async () =>
        Response.json({ items: [], pagination: { total_pages: 1001 } }))
    await assert.rejects(getProviders(), /giới hạn/)
})

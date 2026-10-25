import assert from 'node:assert/strict'
import test from 'node:test'
import { clearSearchViewCache, readSearchView, saveSearchView } from '../src/pages/search/searchViewCache.ts'

test('recent page snapshots are bounded, expire and never cross actor/query/page keys', () => {
    clearSearchViewCache()
    const first = { items: [{ snippet: 'one' }] }
    saveSearchView('alice:query:page1', first, 100)
    assert.equal(readSearchView('alice:query:page1', 101), first)
    assert.equal(readSearchView('bob:query:page1', 101), null)
    assert.equal(readSearchView('alice:query:page2', 101), null)
    assert.equal(readSearchView('alice:query:page1', 60_100), null)
    for (let i = 0; i < 9; i++) saveSearchView(`key${i}`, first, 200)
    assert.equal(readSearchView('key0', 201), null)
    assert.equal(readSearchView('key8', 201), first)
    saveSearchView('huge', { items: ['x'.repeat(250_001)] }, 200)
    assert.equal(readSearchView('huge', 201), null)
    clearSearchViewCache()
    assert.equal(readSearchView('key8', 201), null)
})

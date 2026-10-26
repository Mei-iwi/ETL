import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from '../../frontend/node_modules/typescript/lib/typescript.js'

function cache() {
    const source = fs.readFileSync(new URL('../../frontend/src/pages/search/searchViewCache.ts', import.meta.url), 'utf8')
    const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
    const context = { exports: {} }
    vm.runInNewContext(output, context)
    return context.exports
}

test('metadata responses cannot bypass live revision/ACL checks through tab cache', () => {
    const c = cache()
    c.saveSearchView('old', { items: [], total: 0 }, 0)
    c.saveSearchView('metadata', { branch_status: { metadata: 'READY' }, items: [{ snippet: 'old description' }] }, 0)
    assert.equal(c.readSearchView('metadata', 1), null)
    assert.equal(c.readSearchView('old', 1), null)
})

test('legacy cache retention and TTL are preserved when metadata is OFF', () => {
    const c = cache()
    const value = { items: [], total: 0 }
    c.saveSearchView('user-a-query', value, 0)
    assert.equal(c.readSearchView('user-a-query', 1), value)
    assert.equal(c.readSearchView('user-b-query', 1), null)
    assert.equal(c.readSearchView('user-a-query', 60000), null)
})

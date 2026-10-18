import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

const require = createRequire(new URL('../package.json', import.meta.url))
const ts = require('typescript')
const { renderToStaticMarkup } = require('react-dom/server')
const source = readFileSync(new URL('../src/components/CatalogNotice.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText
const exports = {}
vm.runInNewContext(compiled, { exports, require: name => name === '../services/libraryLookupApi'
    ? require('./src/services/libraryLookupApi.ts') : require(name) })
const full = { completeness: 'ALL_SOURCE_PLUS_LOCAL', source_status: 'SOURCE_OK' }
const fallback = { completeness: 'PARTIAL_FALLBACK', source_status: 'SOURCE_UNAVAILABLE' }
function render(provider, type) {
    const providers = Object.assign([], { catalog: provider })
    const types = Object.assign([], { catalog: type })
    return renderToStaticMarkup(exports.CatalogNotice({ providers, types }))
}

test('healthy catalogs do not display redundant success descriptions', () => {
    assert.equal(render(full, full), '')
    assert.equal(render(full, { completeness: 'MASTER_MANAGED' }), '')
})

test('fallback and unknown catalog coverage remain visible independently', () => {
    const html = render(full, fallback)
    assert.ok(html.includes('role="status"'))
    assert.ok(html.includes('Loại học liệu:'))
    assert.ok(html.includes('Dữ liệu dự phòng'))
    assert.ok(!html.includes('Nhà cung cấp:'))
    assert.ok(!html.includes('Đầy đủ từ SOURCE'))
    assert.ok(render(undefined, undefined).includes('Chưa xác minh được SOURCE'))
})

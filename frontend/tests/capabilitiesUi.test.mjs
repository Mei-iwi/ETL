// Render the real page against API-shaped fixtures, without a Dev server/model.
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

const require = createRequire(new URL('../package.json', import.meta.url))
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const source = readFileSync(new URL('../src/pages/system/SystemCapalitiesPage.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText

function render(retrieval, vector = {}) {
    const data = {
        api_version: 'v1', formats: [], master_sync: { modes: [] },
        processing: { chunking: {} }, representations: { vector }, retrieval, storage: {},
    }
    const states = [data, false, '']
    const exports = {}
    vm.runInNewContext(compiled, { exports, require: name => {
        if (name.endsWith('.css')) return { default: {} }
        if (name === 'react') return { ...React,
            useState: () => [states.shift(), () => {}], useEffect: () => {},
            useRef: () => ({ current: null }), useCallback: fn => fn,
        }
        return require(name)
    } })
    return renderToStaticMarkup(exports.SystemCapalitiesPage())
}

test('capabilities renders API methods, filters, context and actual runtime states', () => {
    const retrieval = {
        modes: ['bow', 'vector', 'hybrid'], filters: ['subject_id', 'grade_level_id'],
        methods: [
            { mode: 'bow', runtime_status: 'NOT_REQUIRED' },
            { mode: 'vector', runtime_status: 'NOT_VERIFIED' },
            { mode: 'hybrid', runtime_status: 'BLOCKED_EMBEDDING_RUNTIME' },
        ], context_supported: true, pagination_supported: true, access_policy: 'LOCAL_DEMO_ONLY',
    }
    const html = render(retrieval, { runtime_status: 'NOT_VERIFIED', configured: true,
        runtime_verified: false, runtime_scope: 'current_process' })
    for (const text of ['BoW', 'Vector', 'Hybrid', 'Không cần model', 'Đã cấu hình model',
        'Model chưa khả dụng', 'Môn học, Khối lớp', 'Xem ngữ cảnh', 'Phân trang kết quả',
        'chưa xác minh hoạt động',
        ]) assert.ok(html.includes(text), text)
    assert.ok(!html.includes('Chỉ thử nghiệm Dev local'))
    assert.ok(!html.includes('Tải lại trang không tự nạp hoặc chạy model'))
    assert.ok(!html.includes('Chưa mở tìm kiếm công khai'))
    retrieval.methods[1].runtime_status = 'READY'
    assert.ok(render(retrieval, { enabled: true }).includes('Model đã chạy thành công'))
    assert.ok(render(retrieval).includes('Model hoạt động'))
})

test('capabilities does not invent methods when API lists none; supports older responses', () => {
    assert.ok(render({ modes: [], filters: [] }).includes('Chưa tích hợp tìm kiếm'))
    const html = render({ modes: ['bow'], filters: ['future_filter'] })
    assert.ok(html.includes('BoW'))
    assert.ok(!html.includes('Hybrid'))
    assert.ok(html.includes('future_filter'))
})

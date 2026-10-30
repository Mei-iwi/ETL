import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from '../../frontend/node_modules/typescript/lib/typescript.js'

const source = fs.readFileSync(new URL('../../frontend/src/pages/library/fileReadiness.ts', import.meta.url), 'utf8')
const context = { exports: {} }
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context)
const { sourceResolutionAllowsFile, canOpenFile } = context.exports

test('source availability is bound to selected version and authorized content', () => {
    const ready = { version_id: 'v1', resolution_status: 'available', source_record_available: true, content_availability: 'authorized-indexed' }
    assert.equal(sourceResolutionAllowsFile(ready, 'v1'), true)
    assert.equal(sourceResolutionAllowsFile(ready, 'v2'), false)
    assert.equal(sourceResolutionAllowsFile(null, 'v1'), false)
    for (const resolution_status of ['blocked', 'stale', 'unavailable'])
        assert.equal(sourceResolutionAllowsFile({ ...ready, resolution_status }, 'v1'), false)
    assert.equal(sourceResolutionAllowsFile({ ...ready, source_record_available: false }, 'v1'), false)
    assert.equal(sourceResolutionAllowsFile({ ...ready, content_availability: 'metadata-only' }, 'v1'), false)
})

test('metadata-only denies contradictory readiness flags', () => {
    assert.equal(canOpenFile({ processing_status: 'CONTENT_READY', index_status: 'READY', metadata_only: true }), false)
    assert.equal(canOpenFile({ processing_status: 'CONTENT_READY', index_status: 'READY' }), true)
})

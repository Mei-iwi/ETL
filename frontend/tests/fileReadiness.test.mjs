import assert from 'node:assert/strict'
import test from 'node:test'
import { canOpenFile, fileErrorMessage } from '../src/pages/library/fileReadiness.ts'
test('file actions require content AND verified index readiness', () => {
    for (const status of [null, {}, { processing_status: 'CONTENT_READY', index_status: 'PENDING' },
        { processing_status: 'OCR_FAILED', index_status: 'READY' }]) assert.equal(canOpenFile(status), false)
    assert.equal(canOpenFile({ processing_status: 'CONTENT_READY', index_status: 'READY' }), true)
})
test('file errors are explicit and contain no source/credential fallback', () => {
    for (const status of [403, 404, 409, 502]) {
        assert.ok(fileErrorMessage(status).length > 10)
        assert.doesNotMatch(fileErrorMessage(status), /https:|token|object_key/)
    }
    assert.notEqual(fileErrorMessage(403), fileErrorMessage(409))
})


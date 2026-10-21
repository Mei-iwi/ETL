import assert from 'node:assert/strict'
import test from 'node:test'
import { canOpenFile, fileErrorMessage, fileResponseError } from '../src/pages/library/fileReadiness.ts'

test('503 distinguishes disabled feature, temporary policy and upstream outage', async () => {
    const messages = []
    for (const [code, expected] of [
        ['SOURCE_REFERENCE_DISABLED', /chưa được bật/],
        ['SOURCE_TEMP_POLICY_REQUIRED', /chính sách tệp tạm/],
        ['SOURCE_UNAVAILABLE', /Không kết nối được nguồn/],
    ]) {
        const response = new Response(JSON.stringify({ detail: { code, message: 'PRIVATE_CONFIGURATION' } }), { status: 503 })
        const message = await fileResponseError(response)
        assert.match(message, expected)
        assert.doesNotMatch(message, /PRIVATE_CONFIGURATION/)
        messages.push(message)
    }
    assert.equal(new Set(messages).size, 3)
})
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

test('source mutation differs from index readiness without showing raw provider messages', async () => {
    const response = new Response(JSON.stringify({ detail: { code: 'SOURCE_HASH_MISMATCH', message: 'PRIVATE_PATH' } }), { status: 409 })
    const message = await fileResponseError(response)
    assert.match(message, /thay đổi nội dung/)
    assert.notEqual(message, fileErrorMessage(409))
    assert.doesNotMatch(message, /PRIVATE_PATH/)
})


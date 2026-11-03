import assert from 'node:assert/strict'
import test from 'node:test'
import { isProcessingPending, ocrAuditMessage, intakeStatusLabel, intakeStatusTone } from '../src/services/processingApi.ts'
import { startVisiblePolling } from '../src/pages/system/visiblePolling.ts'

test('completed OCR continues polling through finalization retry until content is ready', async t => {
    const states = ['FINALIZATION_PENDING', 'FINALIZING', 'CONTENT_READY']
    const observed = []
    let pending = true
    const visibility = { hidden: false, addEventListener() {}, removeEventListener() {} }
    let finish
    const ready = new Promise(resolve => { finish = resolve })
    const poller = startVisiblePolling(async () => {
        const state = states[observed.length]
        observed.push(state)
        pending = isProcessingPending('COMPLETED', state)
        if (!pending) finish()
    }, () => pending, visibility, { busy: 1, idle: null })
    t.after(() => poller.stop())
    await ready
    assert.deepEqual(observed, states)
    assert.equal(pending, false)
})

test('final errors and valid empty results stop polling and allow the appropriate UI action', () => {
    for (const state of ['FINALIZATION_FAILED', 'NO_EXTRACTABLE_CONTENT', 'CONTENT_READY'])
        assert.equal(isProcessingPending('COMPLETED', state), false)
    assert.equal(isProcessingPending('FAILED', 'OCR_FAILED'), false)
    assert.equal(isProcessingPending('COMPLETED_WITH_ERRORS', 'OCR_FAILED'), false)
})

test('inventory waiting is distinct from missing OCR and untrusted messages are never echoed', () => {
    assert.match(ocrAuditMessage('WAITING_FOR_PROCESS_AUTHORIZATION'), /quyền/)
    assert.notEqual(ocrAuditMessage('WAITING_FOR_PROCESS_AUTHORIZATION'), ocrAuditMessage('ABSENT'))
    assert.equal(ocrAuditMessage('internal-path-or-secret'), '')
    assert.equal(ocrAuditMessage({ status: 'ABSENT' }), '')
})

test('version contract scope is distinguished from application permission', () => {
    const status = 'WAITING_FOR_PROCESS_AUTHORIZATION'
    assert.match(ocrAuditMessage(status, 'SOURCE_OCR_VERSION_NOT_AUTHORIZED'), /OCR/)
    assert.notEqual(ocrAuditMessage(status, 'SOURCE_OCR_VERSION_NOT_AUTHORIZED'), ocrAuditMessage(status))
    assert.equal(ocrAuditMessage(status, 'untrusted-server-message'), ocrAuditMessage(status))
})

test('intake queue OCR and index have distinct safe labels', () => {
    const states = ['RECEIVED', 'CHECKING_OCR', 'OCR_QUEUED', 'OCR_RUNNING', 'INDEXING', 'READY']
    assert.equal(new Set(states.map(intakeStatusLabel)).size, states.length)
    for (const state of states) assert.doesNotMatch(intakeStatusLabel(state), /Chưa xử lý|Chưa bắt đầu/)
    assert.equal(intakeStatusLabel('server-internal-secret'), 'Đang cập nhật trạng thái')
    assert.match(intakeStatusLabel('WAITING_AUTHORIZATION'), /quyền/)
    assert.equal(intakeStatusTone('INDEXING'), 'active')
    assert.equal(intakeStatusTone('PROVENANCE_REQUIRED'), 'active')
    assert.equal(intakeStatusTone('SOURCE_INVALID'), 'error')
    assert.equal(intakeStatusTone('READY'), 'success')
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { readPreviewBlob } from '../src/pages/library/previewBody.ts'

function streamed(parts, headers = {}) {
    let cancelled = false, position = 0
    const response = new Response(new ReadableStream({
        pull(controller) {
            if (position === parts.length) controller.close()
            else controller.enqueue(new Uint8Array(parts[position++]))
        },
        cancel() { cancelled = true },
    }), { headers })
    return { response, cancelled: () => cancelled }
}
for (const length of [undefined, 'invalid', '-1', '2']) {
    test('actual bytes enforce preview budget with Content-Length=' + length, async () => {
        const item = streamed([new Array(8).fill(1), new Array(8).fill(2), new Array(8).fill(3)],
            length === undefined ? {} : { 'Content-Length': length })
        await assert.rejects(readPreviewBlob(item.response, undefined, 10), /30 MB/)
        assert.equal(item.cancelled(), true)
    })
}
test('declared oversized file is cancelled before buffering', async () => {
    const item = streamed([[1]], { 'Content-Length': '999' })
    await assert.rejects(readPreviewBlob(item.response, undefined, 10), /30 MB/)
    assert.equal(item.cancelled(), true)
})
test('small body keeps MIME and exact bytes', async () => {
    const item = streamed([[65, 66], [67]], { 'Content-Type': 'application/pdf' })
    const blob = await readPreviewBlob(item.response, undefined, 10)
    assert.equal(blob.type, 'application/pdf')
    assert.equal(await blob.text(), 'ABC')
})
test('cancelled preview never creates a Blob', async () => {
    const controller = new AbortController()
    controller.abort()
    const item = streamed([[1], [2], [3]])
    await assert.rejects(readPreviewBlob(item.response, controller.signal), { name: 'AbortError' })
    assert.equal(item.cancelled(), true)
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { LIBRARY_FILE_ACCEPT, libraryFileError, mediaPreviewType } from '../src/pages/library/libraryFileFormats.ts'
import { MEDIA_PREVIEW_MAX_BYTES, PREVIEW_MAX_BYTES, readPreviewBlob } from '../src/pages/library/previewBody.ts'

test('both forms accept MP3/MP4 including uppercase filenames', () => {
    for (const name of ['lecture.mp3', 'lecture.mp4', 'LECTURE.MP3', 'LECTURE.MP4'])
        assert.equal(libraryFileError({ name, size: 100 }), null)
    assert.ok(LIBRARY_FILE_ACCEPT.includes('.mp3') && LIBRARY_FILE_ACCEPT.includes('.mp4'))
})
test('unsupported or empty replacement fails before metadata update', () => {
    for (const file of [{ name: 'fake.mp3.exe', size: 100 }, { name: 'empty.mp4', size: 0 }])
        assert.equal(typeof libraryFileError(file), 'string')
    assert.equal(libraryFileError({ name: 'document.pdf', size: 10 }), null)
})
test('player selection uses returned MIME rather than guessed file content', () => {
    assert.equal(mediaPreviewType('audio/mpeg'), 'audio')
    assert.equal(mediaPreviewType('audio/mp3; charset=binary'), 'audio')
    assert.equal(mediaPreviewType('VIDEO/MP4'), 'video')
    for (const mime of ['text/html', 'application/octet-stream', 'image/png', 'video/webm', ''])
        assert.equal(mediaPreviewType(mime), null)
})
test('media has bounded 50MB budget; document budget remains 30MB', async () => {
    assert.equal(MEDIA_PREVIEW_MAX_BYTES, 50 * 1024 * 1024)
    assert.equal(PREVIEW_MAX_BYTES, 30 * 1024 * 1024)
    const response = new Response(new Uint8Array([1, 2, 3]), { headers: {
        'Content-Type': 'video/mp4', 'Content-Length': String(MEDIA_PREVIEW_MAX_BYTES + 1),
    } })
    await assert.rejects(readPreviewBlob(response, undefined, MEDIA_PREVIEW_MAX_BYTES), /50 MB/)
})

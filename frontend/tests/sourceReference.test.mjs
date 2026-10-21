import assert from 'node:assert/strict'
import test from 'node:test'
import { originalSourceUrl } from '../src/pages/library/sourceReference.ts'

const info = { ownership: 'SOURCE_BACKED', provider: 'google_drive', availability: 'UNKNOWN',
    can_open_original: true, original_url: 'https://drive.google.com/file/d/approved-file/view' }

test('original links require backend capability and safe exact origin', () => {
    assert.equal(originalSourceUrl(info), info.original_url)
    assert.equal(originalSourceUrl({ ...info, can_open_original: false }), null)
    assert.equal(originalSourceUrl({ ...info, ownership: 'LOCAL_OWNED' }), null)
    for (const original_url of ['javascript:alert(1)', 'file:///private/file.pdf', 'https://drive.google.com.evil.test/file/d/id/view',
        'https://user:secret@drive.google.com/file/d/id/view', 'https://drive.google.com/file/d/id/view?token=private',
        'http://drive.google.com/file/d/id/view', 'https://127.0.0.1/file']) {
        assert.equal(originalSourceUrl({ ...info, original_url }), null)
    }
})

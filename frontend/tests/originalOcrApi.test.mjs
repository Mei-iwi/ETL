import assert from 'node:assert/strict'
import test from 'node:test'
import { getOriginalOCRPage, ProcessingApiError } from '../src/services/processingApi.ts'

test('original OCR page request is lazy, encoded and supports a valid empty page', async t => {
    let request
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        request = { url: String(url), signal: options.signal }
        return Response.json({ job_id: 'job-1', page_num: 2, total_pages: 3, status: 'SUCCEEDED', available: true,
            text: '', offset: 0, total_characters: 0 })
    })
    const controller = new AbortController()
    const result = await getOriginalOCRPage('v/1', 'job-1', 2, 0, controller.signal)
    assert.match(request.url, /v%2F1\/ocr-pages\/2\?job_id=job-1&offset=0&limit=10000$/)
    assert.equal(request.signal, controller.signal)
    assert.equal(result.available, true)
    assert.equal(result.text, '')
})

test('pending pages and denied reads are distinguished', async t => {
    const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ job_id: 'job-1', page_num: 1, total_pages: 1,
        status: 'PENDING', available: false, text: null, offset: 0, total_characters: null }))
    assert.equal((await getOriginalOCRPage('v1', 'job-1', 1)).available, false)
    fetch.mock.mockImplementation(async () => Response.json({ detail: 'Không có quyền' }, { status: 403 }))
    await assert.rejects(getOriginalOCRPage('v1', 'job-1', 1), error => error instanceof ProcessingApiError && error.status === 403)
})

test('mismatched page or impossible empty content is rejected', async t => {
    t.mock.method(globalThis, 'fetch', async () => Response.json({ job_id: 'job-1', page_num: 1, total_pages: 1,
        status: 'SUCCEEDED', available: true, text: null, offset: 0, total_characters: 0 }))
    await assert.rejects(getOriginalOCRPage('v1', 'job-1', 1), /không hợp lệ/)
})

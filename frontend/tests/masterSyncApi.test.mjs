import assert from 'node:assert/strict'
import test from 'node:test'
import { getSourceSyncFiles, syncMasterData } from '../src/services/masterSyncApi.ts'

test('file totals are requested on navigation and omitted from lightweight polling', async t => {
    const urls = []
    t.mock.method(globalThis, 'fetch', async url => { urls.push(String(url)); return Response.json({ items: [] }) })
    await getSourceSyncFiles(null, undefined, { size: 7, includeTotal: true })
    await getSourceSyncFiles(null, undefined, { size: 7 })
    assert.match(urls[0], /include_total=true/)
    assert.doesNotMatch(urls[1], /include_total/)
})

test('sync shows the actionable identity conflict instead of a generic ETL failure', async t => {
    const message = 'SOURCE và MASTER có cùng ID nhưng khác thời điểm tạo. Cần đối soát nguồn đã được tạo lại trước khi đồng bộ.'
    t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: {
        code: 'BLOCKED_SOURCE_IDENTITY', message,
    } }, { status: 409 }))
    await assert.rejects(syncMasterData({ stream: 'all', full: true }), error => error.message === message)
})

test('sync retains legacy text errors and handles malformed error bodies', async t => {
    let structured = true
    t.mock.method(globalThis, 'fetch', async () => Response.json(structured ? { detail: 'Sync is already running' } : {}, { status: 409 }))
    await assert.rejects(syncMasterData({ stream: 'all', full: false }), /Sync is already running/)
    structured = false
    await assert.rejects(syncMasterData({ stream: 'all', full: false }), /HTTP 409/)
})

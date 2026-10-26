import assert from 'node:assert/strict'
import test from 'node:test'
import { syncMasterData } from '../src/services/masterSyncApi.ts'

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

import assert from 'node:assert/strict'
import test from 'node:test'
import { acceptStatusPoll, configuredModeLabel, selectSyncResults } from '../src/pages/system/masterSyncResults.ts'

const run = (id, mode, status = 'COMPLETED', stream = 'resources', started = '2026-10-14T01:00:00Z') => ({
    run_id: id, stream_name: stream, sync_mode: mode, status, started_at: started,
    finished_at: status === 'RUNNING' ? null : started, records_read: 10,
    inserted: 0, updated: 0, skipped: 10, failed: 0,
})

test('configured Incremental does not relabel actual Full or mix per-stream modes', () => {
    assert.equal(configuredModeLabel(['INCREMENTAL']), 'Incremental')
    const data = selectSyncResults([run('1', 'FULL'), run('2', 'INCREMENTAL', 'FAILED', 'users')], null)
    assert.equal(data.resources.sync_mode, 'FULL')
    assert.equal(data.resources.records_read, 10)
    assert.equal(data.users.sync_mode, 'INCREMENTAL')
    assert.equal(data.users.status, 'FAILED')
    assert.equal(data.users.started_at, '2026-10-14T01:00:00Z')
})

test('no run, unknown mode and running stay distinct from config', () => {
    assert.deepEqual(selectSyncResults([], null), {})
    assert.equal(configuredModeLabel([]), 'Chưa xác định')
    assert.equal(configuredModeLabel(['FULL', 'INCREMENTAL']), 'Theo từng nhóm')
    assert.equal(selectSyncResults([run('1', null, 'RUNNING')], null).resources.status, 'RUNNING')
    assert.equal(selectSyncResults([run('1', null)], null).resources.sync_mode, null)
})

for (const mode of ['FULL', 'INCREMENTAL']) {
    test(`manual ${mode}: old poll cannot overwrite response; same ID reconciles server state`, () => {
        const manual = { mode, response: { resources: { ...run('2', mode), records_read: 1, skipped: 1 } } }
        const old = run('1', 'FULL')
        let result = selectSyncResults([old], manual).resources
        assert.equal(result.run_id, '2')
        assert.equal(result.records_read, 1)
        assert.equal(result.sync_mode, mode)
        assert.equal(result.status, 'PENDING_STATUS')
        assert.equal(result.finished_at, null) // Never invent a server timestamp/status.
        const actual = { ...run('2', mode), records_read: 1, skipped: 1 }
        result = selectSyncResults([old, actual], manual).resources
        assert.equal(result.status, 'COMPLETED')
        assert.equal(result.records_read, 1)
        const newer = run('3', 'INCREMENTAL', 'RUNNING', 'resources', '2026-10-14T02:00:00Z')
        assert.equal(selectSyncResults([actual, newer], manual).resources.run_id, '3')
    })
}

test('disabled Auto does not erase history or a manual run', () => {
    assert.equal(selectSyncResults([run('1', 'FULL')], null).resources.run_id, '1')
})

test('stale, aborted, unmounted or during-submit polls are rejected', () => {
    assert.equal(acceptStatusPoll(true, false, 2, 2, false), true)
    for (const args of [[true, false, 1, 2, false], [true, true, 2, 2, false],
        [false, false, 2, 2, false], [true, false, 2, 2, true]]) {
        assert.equal(acceptStatusPoll(...args), false)
    }
})

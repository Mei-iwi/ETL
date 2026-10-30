import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import { queueAudit, getAuditJobs, getDiscrepancies, getLatestReconciliationReport, getFileEvidence } from '../src/services/reconciliationApi.ts'
import { auditJobLabel, auditViewJobs, metadataMatchLabel, sha256CoverageLabel } from '../src/pages/system/reconciliationDisplay.ts'
import { startVisiblePolling } from '../src/pages/system/visiblePolling.ts'

test('all audit actions use queue endpoints with exact scope and no file repair', async t => {
    const calls = []
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({ url: String(url), ...options })
        return Response.json({ status: 'PENDING', job_id: 'queued' }, { status: 202 })
    })
    await queueAudit('files', 'INCREMENTAL', true)
    await queueAudit('files', 'FULL')
    await queueAudit('subjects', 'FULL', true)
    await queueAudit('users', 'INCREMENTAL', false, 'IDS')
    await queueAudit('all', 'INCREMENTAL')
    assert.deepEqual(calls.map(call => call.url), Array(4).fill('/api/v1/reconciliation/audit-jobs').concat('/api/v1/reconciliation/audit-jobs/all'))
    assert.deepEqual(calls.slice(0, 4).map(call => JSON.parse(call.body).audit_type), ['LIGHT', 'DEEP', 'METADATA', 'IDS'])
    assert.equal(JSON.parse(calls[0].body).repair, false)
    assert.equal(JSON.parse(calls[2].body).repair, true)
    await getAuditJobs('users')
    assert.match(calls[5].url, /newest=true&stream=users/)
    await getFileEvidence('version-id')
    assert.match(calls[6].url, /file-evidence\/version-id$/)
})

test('queue failures retain actionable API errors', async t => {
    t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'INVALID_AUDIT_SCOPE' }, { status: 422 }))
    await assert.rejects(queueAudit('files', 'FULL'), /INVALID_AUDIT_SCOPE/)
})

test('polling aborts stale generations, hidden requests and unmount without duplicate timers', async () => {
    class Visibility extends EventTarget { hidden = false }
    const visibility = new Visibility()
    const calls = []
    const releases = []
    const poll = startVisiblePolling(signal => new Promise(resolve => {
        calls.push(signal)
        releases.push(resolve)
    }), () => true, visibility, { busy: 10, idle: 50 })
    assert.equal(calls.length, 1)
    const refresh = poll.refresh()
    assert.equal(calls[0].aborted, true)
    assert.equal(calls.length, 2)
    visibility.hidden = true
    visibility.dispatchEvent(new Event('visibilitychange'))
    assert.equal(calls[1].aborted, true)
    releases.splice(0).forEach(resolve => resolve())
    await refresh
    await new Promise(resolve => setTimeout(resolve, 20))
    assert.equal(calls.length, 2)
    visibility.hidden = false
    visibility.dispatchEvent(new Event('visibilitychange'))
    assert.equal(calls.length, 3)
    poll.stop()
    assert.equal(calls[2].aborted, true)
    releases.splice(0).forEach(resolve => resolve())
    visibility.dispatchEvent(new Event('visibilitychange'))
    assert.equal(calls.length, 3)
})

test('terminal visible polling releases its timer but revalidates after returning to the tab', async () => {
    class Visibility extends EventTarget { hidden = false }
    const visibility = new Visibility()
    const calls = []
    const poll = startVisiblePolling(async signal => { calls.push(signal) }, () => false,
        visibility, { busy: 5, idle: null })
    await new Promise(resolve => setTimeout(resolve, 20))
    assert.equal(calls.length, 1)
    visibility.hidden = true
    visibility.dispatchEvent(new Event('visibilitychange'))
    await new Promise(resolve => setTimeout(resolve, 20))
    assert.equal(calls.length, 1)
    assert.equal(calls[0].aborted, true)
    visibility.hidden = false
    visibility.dispatchEvent(new Event('visibilitychange'))
    await new Promise(resolve => setTimeout(resolve, 20))
    assert.equal(calls.length, 2)
    poll.stop()
    assert.equal(calls[1].aborted, true)
})

const require = createRequire(new URL('../package.json', import.meta.url))
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const page = readFileSync(new URL('../src/pages/system/DataReconciliationPage.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(page, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText

function render(overrides = {}, apiOverrides = {}) {
    let index = 0
    const updates = new Map()
    const queued = []
    const exports = {}
    const callbacks = []
    vm.runInNewContext(compiled, { exports, require: name => {
        if (name.endsWith('.css')) return { default: {} }
        if (name === 'react') return { ...React,
            useState: initial => {
                const key = index++
                return [Object.hasOwn(overrides, key) ? overrides[key] : initial, value => updates.set(key, value)]
            }, useEffect: () => {}, useRef: initial => ({ current: initial }), useCallback: fn => { callbacks.push(fn); return fn },
        }
        if (name.includes('reconciliationApi')) return {
            queueAudit: async (...args) => {
                queued.push(args); return { job_id: 'new-job', status: 'PENDING' }
            },
            getLatestReconciliationReport: async () => ({ stream_reports: [] }),
            getAuditJobs: async () => ({ items: [{ job_id: 'new-job', status: 'PENDING' }] }),
            getDiscrepancies: async () => ({ items: [], total: 0, total_pages: 0 }),
            getAuditCoverage: async () => null,
            getSourceWriteBackStatus: async () => null,
            ...apiOverrides,
        }
        if (name.includes('reconciliationDisplay')) return {
            auditJobInProgress: job => ['PENDING', 'RUNNING', 'RETRY_WAIT'].includes(job.status),
            auditJobLabel, auditViewJobs, metadataMatchLabel, sha256CoverageLabel,
            auditSummary: () => ({ unverified: 0, hashPending: 0, onlyHashPending: false, observedBytes: null, observedFiles: 0 }),
            formatAuditBytes: () => '—', pendingHash: () => false,
        }
        if (name.includes('ReconciliationSchedule')) return { ReconciliationSchedule: () => null }
        if (name.includes('visiblePolling')) return {}
        if (name.includes('NoticeDialog')) return { NoticeDialog: () => null }
        return require(name)
    } })
    const tree = exports.DataReconciliationPage()
    function elements(node, match) {
        if (!node || typeof node !== 'object') return []
        if (Array.isArray(node)) return node.flatMap(child => elements(child, match))
        return (match(node) ? [node] : []).concat(elements(node.props?.children, match))
    }
    return { tree, updates, queued, callbacks, button: label => elements(tree, node =>
        node.type === 'button' && node.props.children === label)[0] }
}

test('restored dashboard keeps report states and removes the extra job and worker panels', () => {
    for (const status of ['RUNNING', 'PARTIAL', 'COMPLETED', 'FAILED', 'BLOCKED']) {
        const view = render({ 0: { overall_status: status, stream_reports: [] } })
        const html = renderToStaticMarkup(view.tree)
        assert.match(html, new RegExp(status))
        assert.match(html, /Đối soát dữ liệu/)
        assert.match(html, /Chi tiết đối soát/)
        assert.doesNotMatch(html, /Job đối soát gần nhất|Tổng quan worker|chưa có bằng chứng heartbeat/)
    }
})

test('manual deep and metadata repair require confirmation and use queued actions', async () => {
    const deep = render({ 6: 'files', 12: 'FULL' })
    deep.button('Quét đối soát').props.onClick()
    assert.equal(deep.queued.length, 0)
    assert.equal(deep.updates.get(14).title, 'Xác nhận quét toàn diện')
    const repair = render({ 6: 'subjects' })
    repair.button('Đồng bộ lệch').props.onClick()
    assert.equal(repair.queued.length, 0)
    const confirmed = render({ 6: 'subjects', 14: repair.updates.get(14) })
    confirmed.button('Xác nhận').props.onClick()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(Array.from(repair.queued[0]), ['subjects', 'INCREMENTAL', true])
    assert.equal(render({ 6: 'files' }).button('Đồng bộ lệch'), undefined)
    assert.equal(render({ 6: 'all' }).button('Đồng bộ lệch'), undefined)
})

test('running audit does not display a finished percentage or label counts as scan progress', () => {
    const view = render({ 0: { latest_run_id: 'in-flight', overall_status: 'RUNNING',
        overall_match_rate_percent: 100, total_source_records: 57, total_target_records: 76,
        total_matched_records: 76, total_mismatched_records: 0, stream_reports: [] } })
    const html = renderToStaticMarkup(view.tree)
    assert.match(html, /Đang kiểm tra/)
    assert.match(html, /Bản ghi SOURCE \/ MASTER/)
    assert.doesNotMatch(html, /100%/)
    assert.match(html, /không phải tiến độ hoàn thành/)
})

test('pending DEEP is shown by its own job and report, not the previous quick scan', async () => {
    const deep = { job_id: 'deep-pending', stream: 'files', audit_type: 'DEEP', status: 'PENDING', progress: {} }
    const calls = []
    const view = render({ 6: 'files' }, {
        getAuditJobs: async () => ({ items: [deep] }),
        getLatestReconciliationReport: async (stream, signal, ids) => {
            calls.push({ stream, ids })
            return { overall_status: 'NO_RUNS_YET', stream_reports: [] }
        },
        getDiscrepancies: async () => { throw new Error('Must not fetch old LIGHT discrepancies') },
    })
    await view.callbacks[0]()
    assert.equal(calls[0].stream, 'files')
    assert.deepEqual(Array.from(calls[0].ids), ['deep-pending'])
    assert.equal(view.updates.get(15), deep)
    assert.equal(view.updates.get(0).stream_reports.length, 0)
    const html = renderToStaticMarkup(render({ 15: deep, 9: true }).tree)
    assert.match(html, /DEEP SHA-256 · Đang chờ/)
    assert.doesNotMatch(html, /Trạng thái: <strong>Quét nhanh/)
})

test('job, run and discrepancy reads use explicit IDs', async t => {
    const urls = []
    t.mock.method(globalThis, 'fetch', async url => { urls.push(String(url)); return Response.json({ items: [] }) })
    await getAuditJobs('files', undefined, null, ['deep-id'])
    await getLatestReconciliationReport('files', undefined, ['deep-id'])
    await getDiscrepancies({ runIds: ['deep-id'] })
    assert.match(urls[0], /job_ids=deep-id/)
    assert.match(urls[1], /run_ids=deep-id/)
    assert.match(urls[2], /run_ids=deep-id/)
    assert.doesNotMatch(urls[2], /latest_only/)
})

test('latest LIGHT and retained SHA coverage render separately without changing selected FULL', async () => {
    const light = { job_id: 'light', audit_type: 'LIGHT', stream: 'files', status: 'PARTIAL', progress: {}, created_at: '2026-10-28T01:00:00Z' }
    const deep = { ...light, job_id: 'deep', audit_type: 'DEEP', status: 'COMPLETED' }
    const coverage = { total_files: 38, recent_sha256_evidence_count: 38, not_verified_count: 0, mismatched: 0 }
    const view = render({ 6: 'files', 12: 'FULL', 15: deep, 16: coverage, 17: light }, {
        getAuditJobs: async () => ({ items: [light, deep] }),
        getAuditCoverage: async () => coverage,
    })
    const html = renderToStaticMarkup(view.tree)
    assert.match(html, /Lượt đối soát mới nhất/)
    assert.match(html, /LIGHT \(quét nhanh\)/)
    assert.match(html, /Lượt đang xem: <strong>DEEP SHA-256/)
    assert.match(html, /38\/38 tệp \(100%\)/)
    assert.match(html, /value="FULL" selected/)
    await view.callbacks[0]()
    assert.equal(view.updates.get(16), coverage)
    assert.equal(view.updates.get(17), light)
    assert.equal(view.updates.has(12), false)
})

test('missing optional coverage and partial evidence never invent full repository verification', () => {
    for (const coverage of [null, {}, { total_files: 2, recent_sha256_evidence_count: 1 },
        { total_files: 2, recent_sha256_evidence_count: 0, mismatched: 1, not_verified_count: 2 }]) {
        const html = renderToStaticMarkup(render({ 6: 'files', 16: coverage }).tree)
        assert.match(html, /Độ bao phủ SHA-256/)
        assert.doesNotMatch(html, /100%/)
    }
})

test('schedule has no duplicate scan buttons and original error routes remain available', () => {
    const schedules = readFileSync(new URL('../src/pages/system/ReconciliationSchedule.tsx', import.meta.url), 'utf8')
    const routes = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
    assert.doesNotMatch(page, /triggerReconciliation/)
    assert.doesNotMatch(schedules, /audit-jobs|submitAll\(true\)|submit\(plan, true\)/)
    assert.match(routes, /path='retry' element={<LibrarySyncErrorsPage/)
    assert.match(routes, /path='blocked' element={<LibrarySyncErrorsPage/)
})

test('schedule checkbox persists on/off only after saving and never enqueues a scan', async t => {
    const source = readFileSync(new URL('../src/pages/system/ReconciliationSchedule.tsx', import.meta.url), 'utf8')
    const scheduleCode = ts.transpileModule(source, { compilerOptions: {
        module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText
    const calls = []
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({ url, ...options }); return Response.json(options?.method === 'PUT' ? { status: 'CONFIGURED' } : { items: [] })
    })
    for (const enabled of [false, true]) {
        const plans = [{ stream: 'subjects', enabled: !enabled, interval_seconds: 60, mode: 'FULL', repair: false }]
        let index = 0
        const updates = new Map()
        const exports = {}
        let editor
        vm.runInNewContext(scheduleCode, { exports, fetch: globalThis.fetch, require: name => {
            if (name.endsWith('.css')) return { default: {} }
            if (name === 'react') return { ...React, useEffect: () => {}, useRef: initial => ({ current: initial }), useState: initial => {
                const key = index++
                if (key === 1) editor ??= { plan: exports.normalizePlan(plans[0]), branch: 'thorough' }
                return [key === 0 ? plans : key === 1 ? editor : initial, value => updates.set(key, value)]
            } }
            if (name.includes('NoticeDialog')) return { NoticeDialog: () => null }
            return require(name)
        } })
        const tree = exports.ReconciliationSchedule()
        function find(node, predicate) {
            if (!node || typeof node !== 'object') return []
            if (Array.isArray(node)) return node.flatMap(child => find(child, predicate))
            return (predicate(node) ? [node] : []).concat(find(node.props?.children, predicate))
        }
        const controls = find(tree, node => node.type === exports.CalendarControls)[0]
        const checkbox = find(controls.type(controls.props), node => node.type === 'input' && node.props['aria-label'] === 'Bật Môn học THOROUGH')[0]
        const before = calls.length
        checkbox.props.onChange({ target: { checked: enabled } })
        assert.equal(calls.length, before)
        const draft = updates.get(1)(editor)
        // Apply the draft as React would on its next render.
        Object.assign(editor.plan, draft.plan)
        find(tree, node => node.type === 'button' && node.props.children === 'Lưu')[0].props.onClick()
        await new Promise(resolve => setImmediate(resolve))
        const saved = calls.findLast(call => call.method === 'PUT')
        assert.equal(saved.url, '/api/v1/reconciliation/schedules/subjects')
        assert.equal(saved.method, 'PUT')
        assert.equal(JSON.parse(saved.body).thorough_schedule.enabled, enabled)
    }
})

import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'
import { queueAudit, getAuditJobs, getFileEvidence } from '../src/services/reconciliationApi.ts'
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

const require = createRequire(new URL('../package.json', import.meta.url))
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const page = readFileSync(new URL('../src/pages/system/DataReconciliationPage.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(page, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText

function render(overrides = {}) {
    let index = 0
    const updates = new Map()
    const queued = []
    const exports = {}
    vm.runInNewContext(compiled, { exports, require: name => {
        if (name.endsWith('.css')) return { default: {} }
        if (name === 'react') return { ...React,
            useState: initial => {
                const key = index++
                return [Object.hasOwn(overrides, key) ? overrides[key] : initial, value => updates.set(key, value)]
            }, useEffect: () => {}, useRef: initial => ({ current: initial }), useCallback: fn => fn,
        }
        if (name.includes('reconciliationApi')) return { queueAudit: async (...args) => {
            queued.push(args); return { job_id: 'new-job', status: 'PENDING' }
        } }
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
    return { tree, updates, queued, button: label => elements(tree, node =>
        node.type === 'button' && node.props.children === label)[0] }
}

test('page renders actual pending/running/partial/failed jobs without claiming worker online', () => {
    for (const status of ['PENDING', 'RUNNING', 'RETRY_WAIT', 'PARTIAL', 'COMPLETED', 'FAILED', 'BLOCKED']) {
        const job = { job_id: 'job-1', stream: 'files', audit_type: 'LIGHT', status, progress: {}, error_code: null }
        const view = render({ 3: [job] })
        const html = renderToStaticMarkup(view.tree)
        assert.match(html, new RegExp(status))
        assert.match(html, /LIGHT/)
        assert.match(html, /chưa có bằng chứng heartbeat/)
        assert.doesNotMatch(html, /ACTIVE|100%|SHA-256 verified/)
    }
})

test('manual deep and metadata repair require confirmation and use queued actions', async () => {
    const deep = render({ 0: 'files', 1: 'FULL' })
    deep.button('Xếp hàng quét ngay').props.onClick()
    assert.equal(deep.queued.length, 0)
    assert.equal(deep.updates.get(13).repair, false)
    const confirmed = render({ 0: 'subjects', 13: { repair: true } })
    confirmed.button('Xác nhận').props.onClick()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(Array.from(confirmed.queued[0]), ['subjects', 'INCREMENTAL', true, 'METADATA'])
    assert.equal(render({ 0: 'files' }).button('Đồng bộ ID lệch'), undefined)
    assert.equal(render({ 0: 'subjects', 2: 'IDS' }).button('Đồng bộ ID lệch'), undefined)
})

test('schedule editing has no second scan engine and old error bookmarks target common filters', () => {
    const schedules = readFileSync(new URL('../src/pages/system/ReconciliationSchedule.tsx', import.meta.url), 'utf8')
    const routes = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
    assert.doesNotMatch(page, /triggerReconciliation/)
    assert.doesNotMatch(schedules, /audit-jobs|submitAll\(true\)|submit\(plan, true\)/)
    assert.match(routes, /source-sync\/files\?filter=retry/)
    assert.match(routes, /source-sync\/files\?filter=blocked/)
})

test('schedule checkbox persists on/off only after saving and never enqueues a scan', async t => {
    const source = readFileSync(new URL('../src/pages/system/ReconciliationSchedule.tsx', import.meta.url), 'utf8')
    const scheduleCode = ts.transpileModule(source, { compilerOptions: {
        module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText
    const calls = []
    t.mock.method(globalThis, 'fetch', async (url, options) => {
        calls.push({ url, ...options }); return Response.json({ status: 'CONFIGURED' })
    })
    for (const enabled of [false, true]) {
        const plans = [{ stream: 'subjects', enabled: !enabled, interval_seconds: 60, mode: 'FULL', repair: false }]
        let index = 0
        const updates = new Map()
        const exports = {}
        vm.runInNewContext(scheduleCode, { exports, fetch: globalThis.fetch, require: name => {
            if (name.endsWith('.css')) return { default: {} }
            if (name === 'react') return { ...React, useEffect: () => {}, useState: initial => {
                const key = index++
                return [key === 0 ? plans : initial, value => updates.set(key, value)]
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
        const checkbox = find(tree, node => node.type === 'input' && node.props['aria-label'] === 'Tự động Môn học')[0]
        const before = calls.length
        checkbox.props.onChange({ target: { checked: enabled } })
        assert.equal(calls.length, before)
        const draft = updates.get(0)(plans)
        // Apply the draft as React would on its next render.
        Object.assign(plans[0], draft[0])
        find(tree, node => node.type === 'button' && node.props.children === 'Lưu')[0].props.onClick()
        await new Promise(resolve => setImmediate(resolve))
        const saved = calls.at(-1)
        assert.equal(saved.url, '/api/v1/reconciliation/schedules/subjects')
        assert.equal(saved.method, 'PUT')
        assert.equal(JSON.parse(saved.body).enabled, enabled)
    }
})

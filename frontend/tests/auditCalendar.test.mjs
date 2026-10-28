import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

const require = createRequire(new URL('../package.json', import.meta.url))
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const source = readFileSync(new URL('../src/pages/system/ReconciliationSchedule.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
} }).outputText

function runtime(plans = [], fetcher = () => { throw new Error('Unexpected request') }, editor) {
    let index = 0
    const updates = new Map()
    const exports = {}
    const effects = []
    vm.runInNewContext(compiled, { exports, fetch: fetcher, Intl, AbortController, require: name => {
        if (name.endsWith('.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => key }) }
        if (name === 'react') return { ...React, useEffect: fn => effects.push(fn), useRef: initial => ({ current: initial }),
            useState: initial => {
                const key = index++
                return [key === 0 ? plans : key === 1 ? editor === undefined
                    ? plans[0] ? { plan: exports.normalizePlan(plans[0]), branch: plans[0].mode === 'FULL' ? 'thorough' : 'fast' } : null
                    : editor : initial, value => updates.set(key, value)]
            },
        }
        if (name.includes('NoticeDialog')) return { NoticeDialog: () => null }
        return require(name)
    } })
    return { ...exports, updates, effects }
}
function find(node, predicate) {
    if (!node || typeof node !== 'object') return []
    if (Array.isArray(node)) return node.flatMap(child => find(child, predicate))
    return (predicate(node) ? [node] : []).concat(find(node.props?.children, predicate))
}
const plain = value => JSON.parse(JSON.stringify(value))

test('recommended daily/weekly/monthly branches are disabled and keep repair opt-in', () => {
    const { calendarDefaults } = runtime()
    assert.equal(calendarDefaults('users', 'fast').cadence, 'DAILY')
    assert.equal(calendarDefaults('users', 'thorough').cadence, 'WEEKLY')
    assert.equal(calendarDefaults('files', 'thorough').cadence, 'MONTHLY')
    for (const stream of ['users', 'files']) for (const branch of ['fast', 'thorough']) {
        const value = calendarDefaults(stream, branch)
        assert.equal(value.enabled, false)
        assert.equal(value.repair, false)
        assert.equal(value.timezone, 'Asia/Ho_Chi_Minh')
    }
})

test('calendar controls select cadence, weekday, day and time without generating requests', () => {
    const { CalendarControls, calendarDefaults } = runtime()
    for (const cadence of ['DAILY', 'WEEKLY', 'MONTHLY', 'INTERVAL']) {
        const changes = []
        const tree = CalendarControls({ stream: 'subjects', branch: 'thorough', disabled: false,
            value: { ...calendarDefaults('subjects', 'thorough'), cadence }, onChange: value => changes.push(value) })
        const input = label => find(tree, node => node.props?.['aria-label'] === label)[0]
        input('Bật Môn học THOROUGH').props.onChange({ target: { checked: true } })
        input('Chu kỳ Môn học THOROUGH').props.onChange({ target: { value: 'MONTHLY' } })
        assert.deepEqual(plain(changes.slice(0, 2)), [{ enabled: true }, { cadence: 'MONTHLY' }])
        if (cadence === 'WEEKLY') {
            input('Thứ Môn học THOROUGH').props.onChange({ target: { value: '2' } })
            assert.deepEqual(plain(changes.at(-1)), { weekday: 2 })
        }
        if (cadence === 'MONTHLY') {
            input('Ngày Môn học THOROUGH').props.onChange({ target: { value: '28' } })
            assert.deepEqual(plain(changes.at(-1)), { month_day: 28 })
        }
        if (cadence !== 'INTERVAL') {
            input('Giờ Môn học THOROUGH').props.onChange({ target: { value: '14:45' } })
            assert.deepEqual(plain(changes.at(-1)), { hour: 14, minute: 45 })
        } else {
            input('Khoảng cách Môn học THOROUGH').props.onChange({ target: { value: '300' } })
            assert.deepEqual(plain(changes.at(-1)), { interval_seconds: 300 })
        }
    }
})

test('payload validation blocks invalid calendar values and never sends server-only fields', () => {
    const { calendarPayload, calendarDefaults, schedulePayload } = runtime()
    const value = calendarDefaults('files', 'thorough')
    for (const invalid of [{ month_day: 31 }, { weekday: 7 }, { hour: 24 }, { minute: 60 }, { timezone: 'Invalid/Zone' }])
        assert.throws(() => calendarPayload({ ...value, ...invalid }))
    const advanced = schedulePayload({ stream: 'files', calendar_configured: true,
        fast_schedule: calendarDefaults('files', 'fast'), thorough_schedule: { ...value, next_run_at: 'server-time' } })
    assert.equal(advanced.thorough_schedule.cadence, 'MONTHLY')
    assert.equal('next_run_at' in advanced.thorough_schedule, false)
    assert.equal('calendar_configured' in advanced, false)
    const converted = schedulePayload({ stream: 'subjects', enabled: true, interval_seconds: 60, mode: 'FULL', repair: true })
    assert.equal(converted.thorough_schedule.cadence, 'INTERVAL')
    assert.equal(converted.thorough_schedule.interval_seconds, 60)
    assert.equal(converted.thorough_schedule.enabled, true)
    assert.equal(converted.thorough_schedule.repair, true)
    assert.equal(converted.fast_schedule.enabled, false)
})

test('advanced save sends both branches once, reloads server next time and preserves other drafts', async () => {
    const helper = runtime()
    const first = { stream: 'subjects', calendar_configured: true,
        fast_schedule: { ...helper.calendarDefaults('subjects', 'fast'), enabled: true },
        thorough_schedule: { ...helper.calendarDefaults('subjects', 'thorough'), enabled: true } }
    const untouched = { stream: 'users', enabled: false, mode: 'INCREMENTAL', interval_seconds: 300, repair: false }
    const calls = []
    const saved = { ...first, fast_schedule: { ...first.fast_schedule, next_run_at: '2026-10-29T02:00:00Z' } }
    const api = runtime([first, untouched], async (url, options) => {
        calls.push({ url, ...options })
        return Response.json(options?.method === 'PUT' ? { status: 'CONFIGURED' } : { items: [saved, { ...untouched, interval_seconds: 1800 }] })
    })
    const tree = api.ReconciliationSchedule()
    const save = find(tree, node => node.type === 'button' && node.props.children === 'Lưu')[0]
    save.props.onClick()
    save.props.onClick() // Same-render double click is bounded too.
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(calls.length, 2)
    assert.equal(calls[0].url, '/api/v1/reconciliation/schedules/subjects')
    assert.equal(calls[0].method, 'PUT')
    const payload = JSON.parse(calls[0].body)
    assert.equal(payload.fast_schedule.cadence, 'DAILY')
    assert.equal(payload.thorough_schedule.cadence, 'WEEKLY')
    assert.equal(calls[1].url, '/api/v1/reconciliation/schedules')
    const next = api.updates.get(0)([first, untouched])
    assert.equal(next[0].fast_schedule.next_run_at, saved.fast_schedule.next_run_at)
    assert.equal(next[1].interval_seconds, 300)
})

test('invalid save is shown without a PUT and file controls never offer metadata repair', async () => {
    const helper = runtime()
    const item = { stream: 'files', calendar_configured: true,
        fast_schedule: helper.calendarDefaults('files', 'fast'),
        thorough_schedule: { ...helper.calendarDefaults('files', 'thorough'), month_day: 31 } }
    const api = runtime([item])
    const tree = api.ReconciliationSchedule()
    find(tree, node => node.type === 'button' && node.props.children === 'Lưu')[0].props.onClick()
    await new Promise(resolve => setImmediate(resolve))
    assert.match(api.updates.get(3), /Ngày trong tháng/)
    const controls = helper.CalendarControls({ stream: 'files', branch: 'thorough', value: item.fast_schedule,
        disabled: false, onChange: () => {} })
    assert.equal(find(controls, node => node.props?.['aria-label']?.startsWith('Tự đồng bộ')).length, 0)
    assert.match(renderToStaticMarkup(controls), /DEEP — SHA-256/)
})

test('overview has five simple rows, no repeated form and one setup action per stream', () => {
    const helper = runtime()
    const plans = ['grades', 'subjects', 'users', 'resources', 'files'].map(stream => ({ stream, calendar_configured: true,
        mode: 'INCREMENTAL', enabled: false, interval_seconds: 1800, repair: false,
        fast_schedule: { ...helper.calendarDefaults(stream, 'fast'), enabled: true },
        thorough_schedule: helper.calendarDefaults(stream, 'thorough') }))
    const api = runtime(plans, undefined, null)
    const tree = api.ReconciliationSchedule()
    const rows = find(tree, node => node.type === 'tr' && node.props['data-audit-stream'])
    assert.equal(rows.length, 5)
    assert.equal(find(tree, node => node.type === 'th').length, 3)
    assert.equal(find(tree, node => node.type === 'select').length, 0)
    assert.equal(find(tree, node => node.type === 'button' && node.props.children === 'Lưu').length, 0)
    assert.equal(find(tree, node => node.type === 'button' && node.props.children === 'Thiết lập').length, 5)
    assert.doesNotMatch(renderToStaticMarkup(tree), /Cấu hình nâng cao|Lưu chung|Theo từng lịch/)
    const setup = find(tree, node => node.props?.['aria-label'] === 'Thiết lập Môn học')[0]
    setup.props.onClick()
    assert.equal(api.updates.get(1).plan.stream, 'subjects')
    const selected = runtime(plans, undefined, api.updates.get(1))
    const open = selected.ReconciliationSchedule()
    assert.equal(find(open, node => node.type === selected.CalendarControls).length, 1)
    assert.equal(find(open, node => node.type === 'button' && node.props.children === 'Lưu').length, 1)
    assert.equal(find(open, node => node.type === 'select' && node.props['aria-label'] === 'Chọn lịch cấu hình').length, 1)
    find(open, node => node.type === 'button' && node.props.children === 'Hủy')[0].props.onClick()
    assert.equal(selected.updates.get(1), null)
})

test('shared form changes only the draft and retains both schedule branches', () => {
    const helper = runtime()
    const item = { stream: 'subjects', calendar_configured: true,
        fast_schedule: { ...helper.calendarDefaults('subjects', 'fast'), enabled: true, hour: 5, repair: true },
        thorough_schedule: { ...helper.calendarDefaults('subjects', 'thorough'), weekday: 3, hour: 8 } }
    const editor = { plan: item, branch: 'fast' }
    const api = runtime([item], undefined, editor)
    const tree = api.ReconciliationSchedule()
    const select = find(tree, node => node.type === 'select' && node.props['aria-label'] === 'Chọn lịch cấu hình')[0]
    select.props.onChange({ target: { value: 'thorough' } })
    const next = api.updates.get(1)(editor)
    assert.equal(next.branch, 'thorough')
    assert.equal(next.plan.fast_schedule.hour, 5)
    assert.equal(next.plan.thorough_schedule.weekday, 3)
    const selected = runtime([item], undefined, next)
    const open = selected.ReconciliationSchedule()
    find(open, node => node.type === selected.CalendarControls)[0].props.onChange({ enabled: true, hour: 10 })
    const draft = selected.updates.get(1)(next)
    assert.equal(draft.plan.fast_schedule.hour, 5)
    assert.equal(draft.plan.fast_schedule.repair, true)
    assert.equal(draft.plan.thorough_schedule.enabled, true)
    assert.equal(draft.plan.thorough_schedule.hour, 10)
    assert.equal(item.thorough_schedule.hour, 8)
    assert.equal(selected.updates.has(0), false) // Persisted overview is unchanged until Save succeeds.
})

test('compact summary reflects only enabled schedules and preserves legacy intervals', () => {
    const api = runtime()
    assert.deepEqual(plain(api.scheduleSummary({ stream: 'users', enabled: false })), ['Chưa bật'])
    assert.deepEqual(plain(api.scheduleSummary({ stream: 'files', enabled: true, mode: 'FULL', interval_seconds: 300 })), ['DEEP — SHA-256 · 5 phút'])
    const item = { stream: 'subjects', calendar_configured: true,
        fast_schedule: { ...api.calendarDefaults('subjects', 'fast'), enabled: true },
        thorough_schedule: { ...api.calendarDefaults('subjects', 'thorough'), enabled: true } }
    const result = api.scheduleSummary(item)
    assert.equal(result.length, 2)
    assert.match(result[0], /Incremental · Hằng ngày 02:00/)
    assert.match(result[1], /Full metadata \+ IDS · Chủ nhật 03:00/)
})

test('all prior interval schedules open in one form with no old configuration option', () => {
    const helper = runtime()
    for (const stream of ['grades', 'subjects', 'users', 'resources', 'files']) {
        for (const mode of ['INCREMENTAL', 'FULL']) for (const enabled of [false, true]) {
            const stored = { stream, mode, enabled, interval_seconds: 300, repair: true }
            const normalized = helper.normalizePlan(stored)
            const branch = mode === 'FULL' ? 'thorough' : 'fast'
            const value = normalized[`${branch}_schedule`]
            assert.equal(value.cadence, 'INTERVAL')
            assert.equal(value.interval_seconds, 300)
            assert.equal(value.enabled, enabled)
            assert.equal(value.repair, stream !== 'files')
            assert.equal(normalized[branch === 'fast' ? 'thorough_schedule' : 'fast_schedule'].enabled, false)
            assert.equal(stored.calendar_configured, undefined)
            const api = runtime([stored], undefined, { plan: normalized, branch })
            const tree = api.ReconciliationSchedule()
            const select = find(tree, node => node.type === 'select' && node.props['aria-label'] === 'Chọn lịch cấu hình')[0]
            assert.deepEqual(find(select, node => node.type === 'option').map(option => option.props.value), ['fast', 'thorough'])
            assert.doesNotMatch(renderToStaticMarkup(tree), /Lịch phút\/giờ hiện tại|cấu hình cũ/)
        }
    }
})

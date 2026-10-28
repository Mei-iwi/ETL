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

function runtime(plans = [], fetcher = () => { throw new Error('Unexpected request') }) {
    let index = 0
    const updates = new Map()
    const exports = {}
    const effects = []
    vm.runInNewContext(compiled, { exports, fetch: fetcher, Intl, AbortController, require: name => {
        if (name.endsWith('.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => key }) }
        if (name === 'react') return { ...React, useEffect: fn => effects.push(fn), useRef: initial => ({ current: initial }),
            useState: initial => {
                const key = index++
                return [key === 0 ? plans : initial, value => updates.set(key, value)]
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
    assert.deepEqual(plain(schedulePayload({ stream: 'subjects', enabled: true, interval_seconds: 60, mode: 'FULL', repair: true })),
        { enabled: true, interval_seconds: 60, mode: 'FULL', repair: true })
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

test('subject and file names use simple bold emphasis and configured modes remain dropdowns', () => {
    const helper = runtime()
    const plans = ['subjects', 'files', 'users'].map(stream => ({ stream, calendar_configured: true,
        fast_schedule: { ...helper.calendarDefaults(stream, 'fast'), enabled: true },
        thorough_schedule: helper.calendarDefaults(stream, 'thorough') }))
    const api = runtime(plans)
    const tree = api.ReconciliationSchedule()
    const rows = find(tree, node => node.type === 'tr' && node.props['data-audit-stream'])
    const subject = rows.find(row => row.props['data-audit-stream'] === 'subjects')
    const files = rows.find(row => row.props['data-audit-stream'] === 'files')
    assert.equal(subject.props.className, undefined)
    assert.equal(files.props.className, undefined)
    assert.equal(rows.find(row => row.props['data-audit-stream'] === 'users').props.className, undefined)
    const subjectHtml = renderToStaticMarkup(subject)
    assert.match(subjectHtml, /Incremental/)
    assert.match(subjectHtml, /Full metadata \+ IDS/)
    assert.match(subjectHtml, /<strong>Môn học<\/strong>/)
    assert.match(renderToStaticMarkup(files), /<strong>Kho tệp<\/strong>/)
    for (const row of [subject, files]) {
        const select = find(row, node => node.type === 'select')[0]
        assert.equal(select.props.value, 'INCREMENTAL')
        assert.equal(select.props.disabled, false)
    }
    assert.match(renderToStaticMarkup(files), /LIGHT/)
    assert.match(renderToStaticMarkup(files), /DEEP — SHA-256/)
    for (const branch of ['fast', 'thorough']) {
        const controls = helper.CalendarControls({ stream: 'subjects', branch,
            value: plans[0][`${branch}_schedule`], disabled: false, onChange: () => {} })
        assert.match(renderToStaticMarkup(controls), /Chế độ quét/)
    }
})

test('mode dropdown enables the selected branches without losing their cadence or repair policy', () => {
    const helper = runtime()
    const item = { stream: 'subjects', calendar_configured: true,
        fast_schedule: { ...helper.calendarDefaults('subjects', 'fast'), enabled: true, hour: 5, repair: true },
        thorough_schedule: { ...helper.calendarDefaults('subjects', 'thorough'), weekday: 3, hour: 8 } }
    for (const [mode, fast, thorough] of [['FULL', false, true], ['BOTH', true, true],
        ['OFF', false, false], ['INCREMENTAL', true, false]]) {
        const api = runtime([item])
        const tree = api.ReconciliationSchedule()
        const select = find(tree, node => node.type === 'select' && node.props['aria-label'] === 'Chế độ Môn học')[0]
        select.props.onChange({ target: { value: mode } })
        const next = api.updates.get(0)([item])[0]
        assert.equal(next.fast_schedule.enabled, fast)
        assert.equal(next.thorough_schedule.enabled, thorough)
        assert.equal(next.fast_schedule.hour, 5)
        assert.equal(next.fast_schedule.repair, true)
        assert.equal(next.thorough_schedule.weekday, 3)
        assert.equal(next.thorough_schedule.hour, 8)
    }
})

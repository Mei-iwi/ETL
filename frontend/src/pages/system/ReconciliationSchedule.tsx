import { useEffect, useRef, useState } from 'react'
import styles from './DataReconciliationPage.module.css'
import { NoticeDialog } from '../../components/NoticeDialog'

export type CalendarPlan = {
    enabled: boolean; cadence: 'INTERVAL' | 'DAILY' | 'WEEKLY' | 'MONTHLY'
    hour: number; minute: number; weekday: number; month_day: number
    timezone: string; interval_seconds: number; repair: boolean; next_run_at?: string | null
}
type Plan = {
    stream: string; enabled: boolean; interval_seconds: number; mode: 'FULL' | 'INCREMENTAL'; repair: boolean
    calendar_configured?: boolean; fast_schedule?: CalendarPlan; thorough_schedule?: CalendarPlan
}
const labels: Record<string, string> = { grades: 'Khối lớp', subjects: 'Môn học', users: 'Người dùng', resources: 'Học liệu', files: 'Kho tệp' }
const intervals = [60, 300, 600, 900, 1200, 1800, 3600]
const intervalLabel = (seconds: number) => seconds === 3600 ? '1 tiếng' : `${seconds / 60} phút`

export function scanModeLabel(stream: string, branch: 'fast' | 'thorough') {
    return stream === 'files' ? branch === 'fast' ? 'LIGHT' : 'DEEP — SHA-256'
        : branch === 'fast' ? 'Incremental' : 'Full metadata + IDS'
}

export function calendarDefaults(stream: string, branch: 'fast' | 'thorough'): CalendarPlan {
    return { enabled: false, cadence: branch === 'fast' ? 'DAILY' : stream === 'files' ? 'MONTHLY' : 'WEEKLY',
        hour: branch === 'fast' ? 2 : 3, minute: 0, weekday: 6, month_day: 1,
        timezone: 'Asia/Ho_Chi_Minh', interval_seconds: 1800, repair: false }
}
export function calendarPayload(value: CalendarPlan) {
    if (!['INTERVAL', 'DAILY', 'WEEKLY', 'MONTHLY'].includes(value.cadence)) throw new Error('Chu kỳ không hợp lệ.')
    for (const [name, number, max, min] of [
        ['Giờ', value.hour, 23, 0], ['Phút', value.minute, 59, 0],
        ['Thứ', value.weekday, 6, 0], ['Ngày trong tháng', value.month_day, 28, 1],
    ] as const) {
        if (!Number.isInteger(number) || number < min || number > max) throw new Error(`${name} phải từ ${min} đến ${max}.`)
    }
    if (!intervals.includes(value.interval_seconds)) throw new Error('Chu kỳ phút/giờ không hợp lệ.')
    try { new Intl.DateTimeFormat('vi-VN', { timeZone: value.timezone }) }
    catch { throw new Error('Múi giờ không hợp lệ. Ví dụ: Asia/Ho_Chi_Minh.') }
    return { enabled: value.enabled, cadence: value.cadence, hour: value.hour, minute: value.minute,
        weekday: value.weekday, month_day: value.month_day, timezone: value.timezone,
        interval_seconds: value.interval_seconds, repair: value.repair }
}
export function schedulePayload(plan: Plan) {
    if (plan.calendar_configured) return {
        fast_schedule: calendarPayload(plan.fast_schedule ?? calendarDefaults(plan.stream, 'fast')),
        thorough_schedule: calendarPayload(plan.thorough_schedule ?? calendarDefaults(plan.stream, 'thorough')),
    }
    return { enabled: plan.enabled, interval_seconds: plan.interval_seconds, mode: plan.mode, repair: plan.repair }
}

async function readPlans(signal?: AbortSignal) {
    const response = await fetch('/api/v1/reconciliation/schedules', { signal })
    if (!response.ok) throw new Error('Không tải được lịch đối soát')
    return (await response.json() as { items: Plan[] }).items
}
async function savePlan(stream: string, payload: object) {
    const response = await fetch(`/api/v1/reconciliation/schedules/${stream}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    })
    if (!response.ok) {
        const body = await response.json().catch(() => null)
        const detail = Array.isArray(body?.detail) ? body.detail.map((item: { msg: string }) => item.msg).join('; ') : body?.detail
        throw new Error(typeof detail === 'string' ? detail : `Không lưu được lịch (HTTP ${response.status})`)
    }
}

type Editor = { plan: Plan; branch: 'legacy' | 'fast' | 'thorough' }

export function scheduleSummary(plan: Plan) {
    if (!plan.calendar_configured) return plan.enabled
        ? [`${plan.stream === 'files' ? plan.mode === 'FULL' ? 'DEEP' : 'LIGHT' : plan.mode === 'FULL' ? 'Full metadata' : 'Incremental'} · ${intervalLabel(plan.interval_seconds)}`]
        : ['Chưa bật']
    const summaries = (['fast', 'thorough'] as const).flatMap(branch => {
        const value = plan[branch === 'fast' ? 'fast_schedule' : 'thorough_schedule']
        if (!value?.enabled) return []
        const cadence = value.cadence === 'DAILY' ? 'Hằng ngày' : value.cadence === 'WEEKLY'
            ? ['Thứ hai', 'Thứ ba', 'Thứ tư', 'Thứ năm', 'Thứ sáu', 'Thứ bảy', 'Chủ nhật'][value.weekday]
            : value.cadence === 'MONTHLY' ? `Ngày ${value.month_day} mỗi tháng` : intervalLabel(value.interval_seconds)
        const time = value.cadence === 'INTERVAL' ? '' : ` ${String(value.hour).padStart(2, '0')}:${String(value.minute).padStart(2, '0')}`
        return [`${scanModeLabel(plan.stream, branch)} · ${cadence}${time}`]
    })
    return summaries.length ? summaries : ['Chưa bật']
}

export function ReconciliationSchedule() {
    const [plans, setPlans] = useState<Plan[]>([])
    const [editor, setEditor] = useState<Editor | null>(null)
    const [busy, setBusy] = useState<string | null>(null)
    const [notice, setNotice] = useState('')
    const saving = useRef(false)
    useEffect(() => {
        const controller = new AbortController()
        void readPlans(controller.signal).then(items => {
            if (!controller.signal.aborted) setPlans(items)
        }).catch(error => { if (!controller.signal.aborted) setNotice(String(error)) })
        return () => controller.abort()
    }, [])

    function change(values: Partial<Plan>) {
        setEditor(previous => previous ? { ...previous, plan: { ...previous.plan, ...values } } : previous)
    }
    function changeCalendar(values: Partial<CalendarPlan>) {
        setEditor(previous => {
            if (!previous || previous.branch === 'legacy') return previous
            const key = previous.branch === 'fast' ? 'fast_schedule' : 'thorough_schedule'
            return { ...previous, plan: { ...previous.plan, [key]: {
                ...calendarDefaults(previous.plan.stream, previous.branch), ...previous.plan[key], ...values, next_run_at: null,
            } } }
        })
    }
    async function submit() {
        if (!editor || saving.current) return
        saving.current = true
        const plan = { ...editor.plan, calendar_configured: editor.branch !== 'legacy' }
        setBusy(plan.stream)
        try {
            await savePlan(plan.stream, schedulePayload(plan))
            const items = await readPlans()
            setPlans(previous => previous.map(row => row.stream === plan.stream
                ? items.find(item => item.stream === row.stream) ?? row : row))
            setEditor(null)
            setNotice('Đã lưu lịch.')
        } catch (error) { setNotice(error instanceof Error ? error.message : 'Không lưu được lịch') }
        finally { saving.current = false; setBusy(null) }
    }

    return <section aria-label="Lịch đối soát độc lập">
        <h3>Lịch đối soát</h3>
        <div className={styles.tableWrap}>
            <table className={styles.table}>
                <thead><tr><th>Luồng</th><th>Lịch đang áp dụng</th><th>Thao tác</th></tr></thead>
                <tbody>{plans.map(plan => <tr key={plan.stream} data-audit-stream={plan.stream}>
                    <td><strong>{labels[plan.stream]}</strong></td>
                    <td>{scheduleSummary(plan).map(line => <div key={line}>{line}</div>)}</td>
                    <td><button className={styles.secondaryButton} disabled={busy !== null}
                        aria-label={`Thiết lập ${labels[plan.stream]}`}
                        aria-expanded={editor?.plan.stream === plan.stream}
                        onClick={() => setEditor({ plan, branch: plan.calendar_configured ? 'fast' : 'legacy' })}>Thiết lập</button></td>
                </tr>)}</tbody>
            </table>
        </div>
        {editor && <div className={styles.scheduleEditor} aria-label={`Thiết lập lịch ${labels[editor.plan.stream]}`}>
            <h4>Thiết lập — {labels[editor.plan.stream]}</h4>
            <label className={styles.scheduleChoice}>Chế độ quét
                <select className={styles.select} aria-label="Chọn lịch cấu hình" value={editor.branch} disabled={busy !== null}
                    onChange={event => setEditor(previous => previous ? { ...previous, branch: event.target.value as Editor['branch'] } : previous)}>
                    {!editor.plan.calendar_configured && <option value="legacy">Lịch phút/giờ hiện tại</option>}
                    <option value="fast">{scanModeLabel(editor.plan.stream, 'fast')} — thường xuyên</option>
                    <option value="thorough">{scanModeLabel(editor.plan.stream, 'thorough')} — toàn diện</option>
                </select>
            </label>
            {editor.branch === 'legacy' ? <fieldset className={styles.schedulePlan} disabled={busy !== null} aria-label="Lịch phút/giờ">
                <label><input aria-label={`Tự động ${labels[editor.plan.stream]}`} type="checkbox" checked={editor.plan.enabled}
                    onChange={event => change({ enabled: event.target.checked })} /> Bật lịch</label>
                <label>Chu kỳ <select className={styles.select} aria-label="Chu kỳ" value={editor.plan.interval_seconds}
                    onChange={event => change({ interval_seconds: Number(event.target.value) })}>
                    {intervals.map(seconds => <option key={seconds} value={seconds}>{intervalLabel(seconds)}</option>)}
                </select></label>
                <label>Chế độ <select className={styles.select} aria-label="Chế độ quét" value={editor.plan.mode}
                    onChange={event => change({ mode: event.target.value as Plan['mode'] })}>
                    <option value="INCREMENTAL">{editor.plan.stream === 'files' ? 'LIGHT' : 'Incremental'}</option>
                    <option value="FULL">{editor.plan.stream === 'files' ? 'DEEP — SHA-256' : 'Full metadata'}</option>
                </select></label>
                {editor.plan.stream !== 'files' && <label><input type="checkbox" checked={editor.plan.repair}
                    aria-label="Tự đồng bộ khi lệch" onChange={event => change({ repair: event.target.checked })} /> Tự đồng bộ khi lệch</label>}
            </fieldset> : <CalendarControls compact stream={editor.plan.stream} branch={editor.branch} disabled={busy !== null}
                value={editor.plan[editor.branch === 'fast' ? 'fast_schedule' : 'thorough_schedule'] ?? calendarDefaults(editor.plan.stream, editor.branch)}
                onChange={changeCalendar} />}
            <div className={styles.actionsBar}>
                <button className={styles.primaryButton} disabled={busy !== null} onClick={() => void submit()}>Lưu</button>
                <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => setEditor(null)}>Hủy</button>
            </div>
        </div>}
        {notice && <NoticeDialog title="Lịch đối soát" message={notice} onClose={() => setNotice('')} />}
    </section>
}

export function CalendarControls({ stream, branch, value, disabled, onChange, compact = false }: {
    stream: string; branch: 'fast' | 'thorough'; value: CalendarPlan; disabled: boolean; onChange: (values: Partial<CalendarPlan>) => void; compact?: boolean
}) {
    const prefix = `${labels[stream]} ${branch === 'fast' ? 'FAST' : 'THOROUGH'}`
    const time = `${String(value.hour).padStart(2, '0')}:${String(value.minute).padStart(2, '0')}`
    return <fieldset className={styles.schedulePlan} disabled={disabled} aria-label={`Lịch ${prefix}`}>
        {!compact && <legend>{branch === 'fast' ? 'Thường xuyên (FAST)' : 'Toàn diện (THOROUGH)'}</legend>}
        {!compact && <label>Chế độ quét <output aria-label={`Chế độ quét ${prefix}`} title={stream === 'files' && branch === 'fast'
            ? 'Chỉ kiểm metadata/kích thước; chưa xác minh SHA-256' : undefined}>
            <strong>{scanModeLabel(stream, branch)}</strong>
        </output></label>}
        <label><input type="checkbox" aria-label={`Bật ${prefix}`} checked={value.enabled}
            onChange={event => onChange({ enabled: event.target.checked })} /> Bật lịch</label>
        <label>Chu kỳ <select className={styles.select} aria-label={`Chu kỳ ${prefix}`} value={value.cadence}
            onChange={event => onChange({ cadence: event.target.value as CalendarPlan['cadence'] })}>
            <option value="DAILY">Hằng ngày</option><option value="WEEKLY">Hằng tuần</option>
            <option value="MONTHLY">Hằng tháng</option><option value="INTERVAL">Phút / giờ</option>
        </select></label>
        {value.cadence === 'INTERVAL' ? <label>Khoảng cách <select className={styles.select} aria-label={`Khoảng cách ${prefix}`}
            value={value.interval_seconds} onChange={event => onChange({ interval_seconds: Number(event.target.value) })}>
            {intervals.map(seconds => <option key={seconds} value={seconds}>{intervalLabel(seconds)}</option>)}
        </select></label> : <>
            {value.cadence === 'WEEKLY' && <label>Thứ <select className={styles.select} aria-label={`Thứ ${prefix}`} value={value.weekday}
                onChange={event => onChange({ weekday: Number(event.target.value) })}>
                {['Thứ hai', 'Thứ ba', 'Thứ tư', 'Thứ năm', 'Thứ sáu', 'Thứ bảy', 'Chủ nhật'].map((day, index) => <option key={day} value={index}>{day}</option>)}
            </select></label>}
            {value.cadence === 'MONTHLY' && <label>Ngày (1–28) <input className={styles.select} type="number" min={1} max={28}
                aria-label={`Ngày ${prefix}`} value={value.month_day} onChange={event => onChange({ month_day: Number(event.target.value) })} /></label>}
            <label>Giờ chạy <input className={styles.select} type="time" aria-label={`Giờ ${prefix}`} value={time}
                onChange={event => { const [hour, minute] = event.target.value.split(':').map(Number); onChange({ hour, minute }) }} /></label>
        </>}
        <details><summary>Múi giờ: {value.timezone}</summary>
            <label>Múi giờ <input className={styles.select} aria-label={`Múi giờ ${prefix}`} value={value.timezone}
                maxLength={64} onChange={event => onChange({ timezone: event.target.value })} /></label>
        </details>
        {stream !== 'files' && <label><input type="checkbox" aria-label={`Tự đồng bộ khi lệch ${prefix}`} checked={value.repair}
            onChange={event => onChange({ repair: event.target.checked })} /> Tự đồng bộ ID metadata lệch</label>}
        <small>Lần tiếp theo: {value.enabled && value.next_run_at ? new Intl.DateTimeFormat('vi-VN', {
            dateStyle: 'short', timeStyle: 'short', timeZone: value.timezone,
        }).format(new Date(value.next_run_at)) : 'Chưa có lịch đã lưu đang bật'}</small>
    </fieldset>
}

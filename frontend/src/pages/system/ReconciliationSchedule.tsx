import { Fragment, useEffect, useRef, useState } from 'react'
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

function calendarMode(plan: Plan) {
    return plan.fast_schedule?.enabled ? plan.thorough_schedule?.enabled ? 'BOTH' : 'INCREMENTAL'
        : plan.thorough_schedule?.enabled ? 'FULL' : 'OFF'
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

export function ReconciliationSchedule() {
    const [plans, setPlans] = useState<Plan[]>([])
    const [common, setCommon] = useState<Omit<Plan, 'stream'>>({ enabled: false, interval_seconds: 1800, mode: 'INCREMENTAL', repair: true })
    const [busy, setBusy] = useState<string | null>(null)
    const [notice, setNotice] = useState('')
    const saving = useRef(false)
    useEffect(() => {
        const controller = new AbortController()
        void readPlans(controller.signal).then(items => {
            if (controller.signal.aborted) return
            setPlans(items)
            const first = items[0]
            if (first) setCommon({ enabled: items.every(plan => plan.enabled),
                interval_seconds: items.every(plan => plan.interval_seconds === first.interval_seconds) ? first.interval_seconds : 1800,
                mode: items.every(plan => plan.mode === first.mode) ? first.mode : 'INCREMENTAL',
                repair: items.filter(plan => plan.stream !== 'files').every(plan => plan.repair) })
        }).catch(error => { if (!controller.signal.aborted) setNotice(String(error)) })
        return () => controller.abort()
    }, [])

    function change(stream: string, values: Partial<Plan>) {
        setPlans(previous => previous.map(plan => plan.stream === stream ? { ...plan, ...values } : plan))
    }
    function changeCalendar(plan: Plan, branch: 'fast' | 'thorough', values: Partial<CalendarPlan>) {
        const key = branch === 'fast' ? 'fast_schedule' : 'thorough_schedule'
        change(plan.stream, { calendar_configured: true,
            [key]: { ...calendarDefaults(plan.stream, branch), ...plan[key], ...values, next_run_at: null } })
    }
    function changeMode(plan: Plan, mode: string) {
        change(plan.stream, {
            fast_schedule: { ...calendarDefaults(plan.stream, 'fast'), ...plan.fast_schedule,
                enabled: mode === 'INCREMENTAL' || mode === 'BOTH', next_run_at: null },
            thorough_schedule: { ...calendarDefaults(plan.stream, 'thorough'), ...plan.thorough_schedule,
                enabled: mode === 'FULL' || mode === 'BOTH', next_run_at: null },
        })
    }

    async function submit(plan: Plan) {
        if (saving.current) return
        saving.current = true
        setBusy(plan.stream)
        try {
            await savePlan(plan.stream, schedulePayload(plan))
            const items = await readPlans()
            // Preserve unsaved drafts in other rows.
            setPlans(previous => previous.map(row => row.stream === plan.stream
                ? items.find(item => item.stream === row.stream) ?? row : row))
            setNotice('Đã lưu lịch. Worker đối soát thực thi độc lập với Auto Sync.')
        } catch (error) { setNotice(error instanceof Error ? error.message : 'Không lưu được lịch') }
        finally { saving.current = false; setBusy(null) }
    }
    async function submitAll() {
        if (saving.current) return
        saving.current = true
        setBusy('all')
        try {
            await savePlan('all', schedulePayload({ stream: 'all', ...common }))
            const items = await readPlans()
            setPlans(items)
            setNotice('Đã lưu chu kỳ phút/giờ chung. Lịch nâng cao đã cấu hình được giữ nguyên.')
        } catch (error) { setNotice(error instanceof Error ? error.message : 'Không lưu được lịch chung') }
        finally { saving.current = false; setBusy(null) }
    }

    return <section aria-label="Lịch đối soát độc lập">
        <h3>Lịch đối soát theo từng luồng</h3>
        <div className={styles.tableWrap}>
            <table className={styles.table}>
                <thead><tr><th>Luồng</th><th>Tự động</th><th>Chu kỳ</th><th>Chế độ quét</th><th title="Chỉ sửa ID metadata đủ điều kiện; không ghi đè tệp sai hash">Tự đồng bộ khi lệch</th><th>Thao tác</th></tr></thead>
                <tbody>
                    <tr data-audit-stream="all">
                        <td><strong>Tất cả luồng</strong></td>
                        <td><input aria-label="Tự động tất cả luồng" type="checkbox" checked={common.enabled}
                            disabled={busy !== null} onChange={event => setCommon(previous => ({ ...previous, enabled: event.target.checked }))} /></td>
                        <td><select className={styles.select} aria-label="Chu kỳ chung" value={common.interval_seconds} disabled={busy !== null}
                            onChange={event => setCommon(previous => ({ ...previous, interval_seconds: Number(event.target.value) }))}>
                            {intervals.map(seconds => <option key={seconds} value={seconds}>{intervalLabel(seconds)}</option>)}
                        </select></td>
                        <td><select className={styles.select} aria-label="Chế độ quét chung" value={common.mode} disabled={busy !== null}
                            onChange={event => setCommon(previous => ({ ...previous, mode: event.target.value as Plan['mode'] }))}>
                            <option value="INCREMENTAL">Incremental / LIGHT</option><option value="FULL">Full metadata / DEEP SHA-256</option>
                        </select></td>
                        <td><input aria-label="Tự đồng bộ metadata khi lệch tất cả luồng" type="checkbox" checked={common.repair} disabled={busy !== null}
                            onChange={event => setCommon(previous => ({ ...previous, repair: event.target.checked }))} /></td>
                        <td><button className={styles.primaryButton} disabled={busy !== null || plans.length !== 5}
                            title="Áp dụng lịch phút/giờ cũ, giữ nguyên lịch nâng cao" onClick={() => void submitAll()}>Lưu chung</button></td>
                    </tr>
                    {plans.map(plan => <Fragment key={plan.stream}>
                        <tr data-audit-stream={plan.stream}>
                            <td><strong>{labels[plan.stream]}</strong></td>
                            <td>{plan.calendar_configured ? 'Theo từng lịch' : <input aria-label={`Tự động ${labels[plan.stream]}`} type="checkbox"
                                checked={plan.enabled} disabled={busy !== null} onChange={event => change(plan.stream, { enabled: event.target.checked })} />}</td>
                            <td>{plan.calendar_configured ? 'Ngày / tuần / tháng' : <select className={styles.select} aria-label={`Chu kỳ ${labels[plan.stream]}`}
                                value={plan.interval_seconds} disabled={busy !== null} onChange={event => change(plan.stream, { interval_seconds: Number(event.target.value) })}>
                                {intervals.map(seconds => <option key={seconds} value={seconds}>{intervalLabel(seconds)}</option>)}
                            </select>}</td>
                            <td>{plan.calendar_configured ? <select className={styles.select} aria-label={`Chế độ ${labels[plan.stream]}`}
                                title="Chọn lịch sử dụng chế độ quét; áp dụng khi bấm Lưu" value={calendarMode(plan)} disabled={busy !== null}
                                onChange={event => changeMode(plan, event.target.value)}>
                                <option value="OFF">Tắt hai lịch</option>
                                <option value="INCREMENTAL">{scanModeLabel(plan.stream, 'fast')}</option>
                                <option value="FULL">{scanModeLabel(plan.stream, 'thorough')}</option>
                                <option value="BOTH">{plan.stream === 'files' ? 'LIGHT + DEEP' : 'Incremental + Full metadata'}</option>
                            </select> : <select className={styles.select} aria-label={`Chế độ ${labels[plan.stream]}`}
                                value={plan.mode} disabled={busy !== null} onChange={event => change(plan.stream, { mode: event.target.value as Plan['mode'] })}>
                                <option value="INCREMENTAL">{plan.stream === 'files' ? 'LIGHT' : 'Incremental'}</option>
                                <option value="FULL">{plan.stream === 'files' ? 'DEEP — SHA-256' : 'Full metadata'}</option>
                            </select>}</td>
                            <td>{plan.stream === 'files' ? 'Chỉ kiểm tra' : plan.calendar_configured ? 'Theo từng lịch' : <input type="checkbox" checked={plan.repair}
                                disabled={busy !== null} aria-label={`Tự đồng bộ khi lệch ${labels[plan.stream]}`}
                                onChange={event => change(plan.stream, { repair: event.target.checked })} />}</td>
                            <td><button className={styles.primaryButton} disabled={busy !== null} onClick={() => void submit(plan)}>Lưu</button></td>
                        </tr>
                        <tr>
                            <td colSpan={6}><details>
                            <summary><strong>Cấu hình nâng cao — {labels[plan.stream]}</strong></summary>
                            <div className={styles.scheduleAdvanced}>
                                {(['fast', 'thorough'] as const).map(branch => <CalendarControls key={branch} stream={plan.stream} branch={branch}
                                    value={plan[branch === 'fast' ? 'fast_schedule' : 'thorough_schedule'] ?? calendarDefaults(plan.stream, branch)}
                                    disabled={busy !== null} onChange={values => changeCalendar(plan, branch, values)} />)}
                            </div>
                        </details></td></tr>
                    </Fragment>)}
                </tbody>
            </table>
        </div>
        {notice && <NoticeDialog title="Lịch đối soát" message={notice} onClose={() => setNotice('')} />}
    </section>
}

export function CalendarControls({ stream, branch, value, disabled, onChange }: {
    stream: string; branch: 'fast' | 'thorough'; value: CalendarPlan; disabled: boolean; onChange: (values: Partial<CalendarPlan>) => void
}) {
    const prefix = `${labels[stream]} ${branch === 'fast' ? 'FAST' : 'THOROUGH'}`
    const time = `${String(value.hour).padStart(2, '0')}:${String(value.minute).padStart(2, '0')}`
    return <fieldset className={styles.schedulePlan} disabled={disabled}>
        <legend>{branch === 'fast' ? 'Thường xuyên (FAST)' : 'Toàn diện (THOROUGH)'}</legend>
        <label>Chế độ quét <output aria-label={`Chế độ quét ${prefix}`} title={stream === 'files' && branch === 'fast'
            ? 'Chỉ kiểm metadata/kích thước; chưa xác minh SHA-256' : undefined}>
            <strong>{scanModeLabel(stream, branch)}</strong>
        </output></label>
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
        <label>Múi giờ <input className={styles.select} aria-label={`Múi giờ ${prefix}`} value={value.timezone}
            maxLength={64} onChange={event => onChange({ timezone: event.target.value })} /></label>
        {stream !== 'files' && <label><input type="checkbox" aria-label={`Tự đồng bộ khi lệch ${prefix}`} checked={value.repair}
            onChange={event => onChange({ repair: event.target.checked })} /> Tự đồng bộ ID metadata lệch</label>}
        <small>Lần tiếp theo: {value.enabled && value.next_run_at ? new Intl.DateTimeFormat('vi-VN', {
            dateStyle: 'short', timeStyle: 'short', timeZone: value.timezone,
        }).format(new Date(value.next_run_at)) : 'Chưa có lịch đã lưu đang bật'}</small>
    </fieldset>
}

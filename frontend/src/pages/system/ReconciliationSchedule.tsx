import { useEffect, useState } from 'react'
import styles from './DataReconciliationPage.module.css'
import { NoticeDialog } from '../../components/NoticeDialog'

type Plan = { stream: string; enabled: boolean; interval_seconds: number; mode: 'FULL' | 'INCREMENTAL'; repair: boolean }
const labels: Record<string, string> = { grades: 'Khối lớp', subjects: 'Môn học', users: 'Người dùng', resources: 'Học liệu', files: 'Kho tệp' }
const intervals = [60, 300, 600, 900, 1200, 1800, 3600]
const intervalLabel = (seconds: number) => seconds === 3600 ? '1 tiếng' : `${seconds / 60} phút`

export function ReconciliationSchedule() {
    const [plans, setPlans] = useState<Plan[]>([])
    const [common, setCommon] = useState<Omit<Plan, 'stream'>>({ enabled: false, interval_seconds: 1800, mode: 'INCREMENTAL', repair: true })
    const [busy, setBusy] = useState<string | null>(null)
    const [notice, setNotice] = useState('')
    useEffect(() => {
        const controller = new AbortController()
        void fetch('/api/v1/reconciliation/schedules', { signal: controller.signal }).then(async response => {
            if (!response.ok) throw new Error('Không tải được lịch đối soát')
            const data = await response.json() as { items: Plan[] }
            if (controller.signal.aborted) return
            setPlans(data.items)
            const first = data.items[0]
            if (first) setCommon({ enabled: data.items.every(plan => plan.enabled),
                interval_seconds: data.items.every(plan => plan.interval_seconds === first.interval_seconds) ? first.interval_seconds : 1800,
                mode: data.items.every(plan => plan.mode === first.mode) ? first.mode : 'INCREMENTAL',
                repair: data.items.filter(plan => plan.stream !== 'files').every(plan => plan.repair) })
        }).catch(error => { if (!controller.signal.aborted) setNotice(String(error)) })
        return () => controller.abort()
    }, [])

    function change(stream: string, values: Partial<Plan>) {
        setPlans(previous => previous.map(plan => plan.stream === stream ? { ...plan, ...values } : plan))
    }

    async function submit(plan: Plan) {
        setBusy(plan.stream)
        try {
            const { stream, ...values } = plan
            const response = await fetch(`/api/v1/reconciliation/schedules/${stream}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(values),
            })
            if (!response.ok) throw new Error(`Không lưu/chạy được đối soát (HTTP ${response.status})`)
            setNotice('Đã lưu lịch trên server. Worker đối soát thực thi cả khi đóng trình duyệt.')
        } catch (error) { setNotice(error instanceof Error ? error.message : 'Không thực hiện được yêu cầu') }
        finally { setBusy(null) }
    }

    async function submitAll() {
        setBusy('all')
        try {
            const response = await fetch('/api/v1/reconciliation/schedules/all', {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(common),
            })
            if (!response.ok) throw new Error(`Không lưu/chạy được đối soát chung (HTTP ${response.status})`)
            setPlans(previous => previous.map(plan => ({ ...plan, ...common,
                repair: plan.stream !== 'files' && common.repair })))
            setNotice('Đã áp dụng cấu hình chung cho tất cả luồng. Có thể lưu cấu hình riêng ở từng dòng.')
        } catch (error) { setNotice(error instanceof Error ? error.message : 'Không thực hiện được yêu cầu chung') }
        finally { setBusy(null) }
    }

    return <section aria-label="Lịch đối soát độc lập">
        <h3>Lịch đối soát theo từng luồng</h3>
        <div className={styles.tableWrap}>
            <table className={styles.table}>
                <thead><tr><th>Luồng</th><th>Tự động</th><th>Chu kỳ</th><th>Chế độ quét</th><th>Đồng bộ lệch</th><th>Thao tác</th></tr></thead>
                <tbody>
                    <tr className={styles.sharedSchedule} data-audit-stream="all">
                        <td><strong>Tất cả luồng</strong></td>
                        <td><input aria-label="Tự động tất cả luồng" type="checkbox" checked={common.enabled}
                            onChange={event => setCommon(previous => ({ ...previous, enabled: event.target.checked }))} /></td>
                        <td><select className={styles.select} aria-label="Chu kỳ chung" value={common.interval_seconds}
                            onChange={event => setCommon(previous => ({ ...previous, interval_seconds: Number(event.target.value) }))}>
                            {intervals.map(seconds => <option key={seconds} value={seconds}>{intervalLabel(seconds)}</option>)}
                        </select></td>
                        <td><select className={styles.select} aria-label="Chế độ quét chung" value={common.mode}
                            onChange={event => setCommon(previous => ({ ...previous, mode: event.target.value as Plan['mode'] }))}>
                            <option value="INCREMENTAL">Incremental</option><option value="FULL">Full</option>
                        </select></td>
                        <td><input aria-label="Đồng bộ metadata lệch tất cả luồng" type="checkbox" checked={common.repair}
                            onChange={event => setCommon(previous => ({ ...previous, repair: event.target.checked }))} /></td>
                        <td><div className={styles.scheduleActions}>
                            <button className={styles.primaryButton} disabled={busy !== null || plans.length !== 5} onClick={() => void submitAll()}>Lưu chung</button>
                        </div></td>
                    </tr>
                    {plans.map(plan => <tr key={plan.stream} data-audit-stream={plan.stream}>
                    <td>{labels[plan.stream]}</td>
                    <td><input aria-label={`Tự động ${labels[plan.stream]}`} type="checkbox" checked={plan.enabled}
                        onChange={event => change(plan.stream, { enabled: event.target.checked })} /></td>
                    <td><select className={styles.select} aria-label={`Chu kỳ ${labels[plan.stream]}`} value={plan.interval_seconds}
                        onChange={event => change(plan.stream, { interval_seconds: Number(event.target.value) })}>
                        {intervals.map(seconds => <option key={seconds} value={seconds}>{intervalLabel(seconds)}</option>)}
                    </select></td>
                    <td><select className={styles.select} aria-label={`Chế độ ${labels[plan.stream]}`} value={plan.mode}
                        onChange={event => change(plan.stream, { mode: event.target.value as Plan['mode'] })}>
                        <option value="INCREMENTAL">Incremental</option><option value="FULL">Full</option>
                    </select></td>
                    <td>{plan.stream === 'files' ? 'Chỉ xác minh' : <input type="checkbox" checked={plan.repair}
                        aria-label={`Đồng bộ lệch ${labels[plan.stream]}`}
                        onChange={event => change(plan.stream, { repair: event.target.checked })} />}</td>
                    <td><div className={styles.scheduleActions}><button className={styles.primaryButton} disabled={busy !== null} onClick={() => void submit(plan)}>Lưu</button>
                        </div></td>
                </tr>)}</tbody>
            </table>
        </div>
        {notice && <NoticeDialog title="Lịch đối soát" message={notice} onClose={() => setNotice('')} />}
    </section>
}

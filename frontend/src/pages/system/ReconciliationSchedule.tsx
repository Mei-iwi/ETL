import { useEffect, useState } from 'react'
import styles from './DataReconciliationPage.module.css'
import { NoticeDialog } from '../../components/NoticeDialog'

type Plan = { stream: string; enabled: boolean; interval_seconds: number; mode: 'FULL' | 'INCREMENTAL'; repair: boolean }
const labels: Record<string, string> = { grades: 'Khối lớp', subjects: 'Môn học', users: 'Người dùng', resources: 'Học liệu', files: 'Kho tệp' }

export function ReconciliationSchedule() {
    const [plans, setPlans] = useState<Plan[]>([])
    const [busy, setBusy] = useState<string | null>(null)
    const [notice, setNotice] = useState('')
    useEffect(() => {
        const controller = new AbortController()
        void fetch('/api/v1/reconciliation/schedules', { signal: controller.signal }).then(async response => {
            if (!response.ok) throw new Error('Không tải được lịch đối soát')
            const data = await response.json() as { items: Plan[] }
            setPlans(data.items)
        }).catch(error => { if (!controller.signal.aborted) setNotice(String(error)) })
        return () => controller.abort()
    }, [])

    function change(stream: string, values: Partial<Plan>) {
        setPlans(previous => previous.map(plan => plan.stream === stream ? { ...plan, ...values } : plan))
    }

    async function submit(plan: Plan, runNow = false) {
        setBusy(plan.stream)
        try {
            const { stream, ...values } = plan
            const response = await fetch(runNow ? '/api/v1/reconciliation/audit-jobs'
                : `/api/v1/reconciliation/schedules/${stream}`, {
                method: runNow ? 'POST' : 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(runNow ? { stream, mode: plan.mode, repair: plan.repair,
                    audit_type: stream === 'files' ? plan.mode === 'FULL' ? 'DEEP' : 'LIGHT' : 'METADATA' } : values),
            })
            if (!response.ok) throw new Error(`Không lưu/chạy được đối soát (HTTP ${response.status})`)
            setNotice(runNow ? 'Đã xếp hàng. Worker đối soát sẽ xử lý riêng luồng đã chọn.'
                : 'Đã lưu lịch trên server. Worker đối soát thực thi cả khi đóng trình duyệt.')
        } catch (error) { setNotice(error instanceof Error ? error.message : 'Không thực hiện được yêu cầu') }
        finally { setBusy(null) }
    }

    return <section aria-label="Lịch đối soát độc lập">
        <h3>Lịch đối soát theo từng luồng</h3>
        <p className={styles.scheduleIntro}>Lịch chạy trên server. Kho tệp: Incremental kiểm tra nhanh, Full xác minh SHA-256 theo lô. Metadata chỉ đồng bộ ID lệch khi bật tùy chọn.</p>
        <div className={styles.tableWrap}>
            <table className={styles.table}>
                <thead><tr><th>Luồng</th><th>Tự động</th><th>Chu kỳ</th><th>Chế độ quét</th><th>Đồng bộ lệch</th><th>Thao tác</th></tr></thead>
                <tbody>{plans.map(plan => <tr key={plan.stream}>
                    <td>{labels[plan.stream]}</td>
                    <td><input aria-label={`Tự động ${labels[plan.stream]}`} type="checkbox" checked={plan.enabled}
                        onChange={event => change(plan.stream, { enabled: event.target.checked })} /></td>
                    <td><select className={styles.select} aria-label={`Chu kỳ ${labels[plan.stream]}`} value={plan.interval_seconds}
                        onChange={event => change(plan.stream, { interval_seconds: Number(event.target.value) })}>
                        <option value={900}>15 phút</option><option value={1200}>20 phút</option>
                        <option value={1800}>30 phút</option><option value={3600}>1 tiếng</option>
                    </select></td>
                    <td><select className={styles.select} aria-label={`Chế độ ${labels[plan.stream]}`} value={plan.mode}
                        onChange={event => change(plan.stream, { mode: event.target.value as Plan['mode'] })}>
                        <option value="INCREMENTAL">Incremental</option><option value="FULL">Full</option>
                    </select></td>
                    <td>{plan.stream === 'files' ? 'Chỉ xác minh' : <input type="checkbox" checked={plan.repair}
                        aria-label={`Đồng bộ lệch ${labels[plan.stream]}`}
                        onChange={event => change(plan.stream, { repair: event.target.checked })} />}</td>
                    <td><div className={styles.scheduleActions}><button className={styles.primaryButton} disabled={busy !== null} onClick={() => void submit(plan)}>Lưu</button>
                        <button className={styles.secondaryButton} disabled={busy !== null} onClick={() => void submit(plan, true)}>Quét luồng này</button></div></td>
                </tr>)}</tbody>
            </table>
        </div>
        {notice && <NoticeDialog title="Lịch đối soát" message={notice} onClose={() => setNotice('')} />}
    </section>
}

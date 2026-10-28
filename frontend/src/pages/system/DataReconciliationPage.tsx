import { useCallback, useEffect, useRef, useState } from 'react'
import { NoticeDialog } from '../../components/NoticeDialog'
import { getAuditCoverage, getAuditJobs, getDiscrepancies, getLatestReconciliationReport, queueAudit } from '../../services/reconciliationApi'
import type { AuditCoverage, AuditJob } from '../../services/reconciliationApi'
import type { DiscrepancyItem, ReconciliationLatestReport } from '../../types/reconciliation'
import { ReconciliationSchedule } from './ReconciliationSchedule'
import { startVisiblePolling } from './visiblePolling'
import styles from './DataReconciliationPage.module.css'

const activeStatuses = new Set(['PENDING', 'RUNNING', 'RETRY_WAIT'])
const scopes: Record<string, string> = { all: 'Tất cả luồng', grades: 'Khối lớp', subjects: 'Môn học',
    users: 'Người dùng', resources: 'Học liệu', files: 'Kho tệp' }

export function DataReconciliationPage() {
    const [stream, setStream] = useState('all')
    const [mode, setMode] = useState<'INCREMENTAL' | 'FULL'>('INCREMENTAL')
    const [kind, setKind] = useState<'METADATA' | 'IDS'>('METADATA')
    const [jobs, setJobs] = useState<AuditJob[]>([])
    const [coverage, setCoverage] = useState<AuditCoverage | null>(null)
    const [report, setReport] = useState<ReconciliationLatestReport | null>(null)
    const [focused, setFocused] = useState<string | null>(null)
    const [discrepancies, setDiscrepancies] = useState<DiscrepancyItem[]>([])
    const [page, setPage] = useState(1)
    const [pages, setPages] = useState(0)
    const [error, setError] = useState('')
    const [notice, setNotice] = useState('')
    const [posting, setPosting] = useState(false)
    const [confirmation, setConfirmation] = useState<{ repair: boolean } | null>(null)
    const [jobCursors, setJobCursors] = useState<Array<string | null>>([null])
    const [jobNext, setJobNext] = useState<string | null>(null)
    const jobAfter = jobCursors[jobCursors.length - 1]
    const postingRef = useRef(false)
    const busy = useRef(false)
    const poller = useRef<ReturnType<typeof startVisiblePolling> | null>(null)

    const load = useCallback(async (signal: AbortSignal) => {
        try {
            const [queued, evidence, latest] = await Promise.all([
                getAuditJobs(stream, signal, jobAfter), getAuditCoverage(signal), getLatestReconciliationReport(stream, signal),
            ])
            const id = focused ?? queued.items[0]?.job_id
            const details = id ? await getDiscrepancies({ runId: id, page, pageSize: 10 }, signal) : null
            if (signal.aborted) return
            setJobs(queued.items)
            setJobNext(queued.next_cursor)
            busy.current = queued.items.some(job => activeStatuses.has(job.status))
            setCoverage(evidence)
            setReport(latest)
            setDiscrepancies(details?.items ?? [])
            setPages(details?.total_pages ?? 0)
            setError('')
        } catch (caught) {
            if (!signal.aborted) setError(caught instanceof Error ? caught.message : 'Không tải được đối soát')
        }
    }, [stream, focused, page, jobAfter])

    useEffect(() => {
        const current = startVisiblePolling(load, () => busy.current)
        poller.current = current
        return () => { current.stop(); poller.current = null }
    }, [load])

    async function run(repair = false) {
        if (postingRef.current) return
        postingRef.current = true
        setPosting(true)
        try {
            const result = await queueAudit(stream, mode, repair,
                stream !== 'all' && stream !== 'files' ? kind : undefined)
            setFocused(result.job_id ?? result.items?.[0]?.job_id ?? null)
            setJobCursors([null])
            setPage(1)
            setNotice('Đã xếp hàng. Worker xử lý nền theo hạn mức; có thể rời trang. Kết quả xác minh chỉ có sau khi worker xử lý.')
        } catch (caught) { setError(caught instanceof Error ? caught.message : 'Không xếp hàng được') }
        finally { postingRef.current = false; setPosting(false) }
    }
    function requestRun(repair = false) {
        if (repair || mode === 'FULL') setConfirmation({ repair })
        else void run()
    }
    const selected = jobs.find(job => job.job_id === focused) ?? (!focused ? jobs[0] : undefined)
    return <div className={styles.container}>
        {notice && <NoticeDialog title="Đối soát" message={notice} onClose={() => setNotice('')} />}
        {error && <p role="alert" className={styles.alertWarning}>{error}</p>}
        <section className={styles.card}>
            <h2>Đối soát &amp; Lịch</h2>
            <p>Lịch được worker thực thi cả khi đóng trình duyệt. Quét thủ công vẫn dùng được khi lịch tắt.</p>
            <div className={styles.statusGrid}>
                <div>Lượt quét gần nhất: {report?.last_reconciled_at ? new Date(report.last_reconciled_at).toLocaleString('vi-VN') : 'Chưa có dữ liệu'}</div>
                <div>Job đối soát: {selected?.status ?? 'Chưa có dữ liệu'}</div>
                <div>Sai lệch của job: {String(selected?.progress.mismatched_count ?? 'Chưa có dữ liệu')}</div>
                <div>SHA-256 còn hiệu lực: {coverage ? `${coverage.recent_sha256_evidence_count}/${coverage.total_files}` : 'Chưa xác minh'}</div>
            </div>
            <p>Bằng chứng kho hiện tại khác với kết quả một lượt quét. LIGHT chỉ kiểm kích thước/revision.</p>
            <p>Lịch mặc định của hệ thống: {coverage ? coverage.scheduler_enabled ? 'đang bật' : 'đang tắt' : 'chưa xác minh'}.
                Lịch riêng bên dưới ưu tiên theo từng luồng; lịch IDS vẫn chạy theo cấu hình hệ thống.</p>
            <label>Phạm vi hiển thị <select className={styles.select} aria-label="Luồng đối soát" value={stream} onChange={e => {
                setStream(e.target.value); setFocused(null); setPage(1); setJobCursors([null])
            }}>{Object.entries(scopes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <details><summary>Thao tác quét thủ công</summary>
                <div className={styles.actionsBar}>
                    <select className={styles.select} aria-label="Phạm vi đối soát" value={mode}
                        onChange={e => setMode(e.target.value as typeof mode)}>
                        <option value="INCREMENTAL">Metadata tăng dần / tệp LIGHT</option>
                        <option value="FULL">Metadata toàn diện / tệp DEEP SHA-256</option>
                    </select>
                    {stream !== 'all' && stream !== 'files' && <select className={styles.select} value={kind}
                        aria-label="Loại đối soát metadata" onChange={e => setKind(e.target.value as typeof kind)}>
                        <option value="METADATA">METADATA</option><option value="IDS">IDS — thiếu/thừa ID</option>
                    </select>}
                    <button className={styles.primaryButton} disabled={posting} onClick={() => requestRun()}>Xếp hàng quét ngay</button>
                    {stream !== 'all' && stream !== 'files' && kind === 'METADATA' &&
                        <button className={styles.secondaryButton} disabled={posting} onClick={() => requestRun(true)}>Đồng bộ ID lệch</button>}
                </div>
            </details>
            <button className={styles.secondaryButton} onClick={() => void poller.current?.refresh()}>Làm mới trạng thái</button>
            <ReconciliationSchedule />
        </section>
        <section className={styles.card}>
            <h3>Job đối soát gần nhất</h3>
            <p>Worker đối soát: chưa có bằng chứng heartbeat trong API này.</p>
            {!jobs.length && <p>Chưa có job trong phạm vi đã chọn.</p>}
            <div className={styles.tableWrap}><table className={styles.table}>
                <thead><tr><th>Luồng</th><th>Loại</th><th>Trạng thái</th><th>Đã quét / khớp / lệch / chưa xác minh</th><th>Repair</th><th>Chi tiết</th></tr></thead>
                <tbody>{jobs.map(job => <tr key={job.job_id}>
                    <td>{scopes[job.stream] ?? job.stream}</td><td>{job.audit_type}</td><td>{job.status}{job.error_code && ` · ${job.error_code}`}</td>
                    <td>{['records_scanned', 'matched_count', 'mismatched_count', 'not_verified_count'].map(key => String(job.progress[key] ?? '—')).join(' / ')}</td>
                    <td>{job.progress.repair_requested ? 'Đã yêu cầu' : 'Chỉ kiểm tra'}</td>
                    <td><button className={styles.secondaryButton} onClick={() => { setFocused(job.job_id); setPage(1) }}>Xem</button>
                        <details><summary>Chi tiết kỹ thuật</summary><p>Job / Run: {job.job_id}</p>
                            {job.next_retry_at && <p>Thử lại: {new Date(job.next_retry_at).toLocaleString('vi-VN')}</p>}
                            <pre>{JSON.stringify(job.progress, null, 2)}</pre></details></td>
                </tr>)}</tbody>
            </table></div>
            <button className={styles.secondaryButton} disabled={jobCursors.length === 1} onClick={() => {
                setJobCursors(values => values.slice(0, -1)); setFocused(null); setPage(1)
            }}>Job mới hơn</button>
            <button className={styles.secondaryButton} disabled={!jobNext} onClick={() => {
                if (jobNext) setJobCursors(values => [...values, jobNext]); setFocused(null); setPage(1)
            }}>Job cũ hơn</button>
            <h3>Sai lệch của job đang xem</h3>
            {!discrepancies.length && <p>Chưa có sai lệch được ghi nhận cho job này; chưa đủ để kết luận toàn bộ kho đã xác minh.</p>}
            <div className={styles.tableWrap}><table className={styles.table}>
                <thead><tr><th>Bản ghi</th><th>Kết quả</th><th>Xử lý</th><th>Bằng chứng</th></tr></thead>
                <tbody>{discrepancies.map(row => <tr key={row.id}>
                    <td>{row.record_id}</td><td>{row.target_value?.reason_code === 'SHA256_NOT_CHECKED'
                        ? 'Đã kiểm kích thước; chưa xác minh SHA-256' : row.discrepancy_flag}</td>
                    <td>{row.resolution_status}</td><td><details><summary>Chi tiết kỹ thuật</summary>
                        <pre>{JSON.stringify({ source: row.source_value, target: row.target_value }, null, 2)}</pre></details></td>
                </tr>)}</tbody>
            </table></div>
            <button className={styles.secondaryButton} disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Trước</button>
            <span> Trang {page}/{pages || 1} </span>
            <button className={styles.secondaryButton} disabled={page >= pages} onClick={() => setPage(p => p + 1)}>Sau</button>
        </section>
        {confirmation && <div className={styles.modalBackdrop}><div className={styles.modalCard} role="dialog" aria-modal="true" aria-label="Xác nhận đối soát">
            <h3>Xác nhận thao tác thủ công</h3>
            <p>{confirmation.repair ? 'Chỉ sửa các ID metadata đủ điều kiện; không xóa MASTER hay ghi đè tệp.'
                : 'Quét toàn diện có thể đọc toàn bộ metadata hoặc tải tệp để băm SHA-256. Worker áp dụng hạn mức từng lượt.'}</p>
            <button className={styles.secondaryButton} onClick={() => setConfirmation(null)}>Hủy</button>
            <button className={styles.primaryButton} onClick={() => { const repair = confirmation.repair; setConfirmation(null); void run(repair) }}>Xác nhận</button>
        </div></div>}
    </div>
}

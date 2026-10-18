import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { listEvaluationRuns } from '../../services/evaluationApi'
import type { EvaluationRunList } from '../../types/evaluation'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'

const statuses: Record<string, string> = {
    RUNNING: 'Đang chạy', COMPLETED: 'Hoàn tất', FAILED: 'Thất bại',
    PARTIAL: 'Hoàn tất một phần', NOT_COMPARABLE: 'Không thể so sánh',
}

export function EvaluationHistory({ selectedId }: { selectedId: string }) {
    const [page, setPage] = useState(1)
    const [attempt, setAttempt] = useState(0)
    const [data, setData] = useState<EvaluationRunList | null>(null)
    const [busy, setBusy] = useState(true)
    const [error, setError] = useState('')
    useEffect(() => {
        const controller = new AbortController()
        void Promise.resolve().then(async () => {
            if (controller.signal.aborted) return
            setBusy(true); setError('')
            try {
                const result = await listEvaluationRuns(page, controller.signal)
                if (!controller.signal.aborted) {
                    if (page > result.total_pages) setPage(result.total_pages)
                    else setData(result)
                }
            } catch (reason) {
                if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được danh sách.')
            } finally {
                if (!controller.signal.aborted) setBusy(false)
            }
        })
        return () => controller.abort()
    }, [page, attempt])
    return <section className={`${layout.card} ${styles.card}`} aria-label="Lịch sử thực nghiệm" aria-busy={busy}>
        <div className={styles.historyHeading}>
            <h2>Thực nghiệm đã chạy</h2>
            <div className={styles.row}>
                <button className={layout.secondaryButton} disabled={busy} onClick={() => setAttempt(n => n + 1)}>Tải lại</button>
                <Link className={layout.primaryButton} to="/experiments/run">Chạy thực nghiệm</Link>
            </div>
        </div>
        {busy && <div role="status" className={styles.pending}>Đang tải danh sách…</div>}
        {error && <div role="alert" className={styles.error}>{error}</div>}
        {!error && data && <>
            {data.items.length === 0 ? <p>Chưa có thực nghiệm.</p> :
                <div className={styles.tableWrap}><table className={styles.table}>
                    <thead><tr><th>Thực nghiệm</th><th>Thời gian chạy</th><th>Thời lượng</th><th>Trạng thái</th><th>Thao tác</th></tr></thead>
                    <tbody>{data.items.map(row => <tr key={row.id} className={row.id === selectedId ? styles.historySelected : undefined}>
                        <td>{row.dataset_name}<div className={styles.muted}>{row.query_count} truy vấn</div></td>
                        <td><time dateTime={row.created_at}>{new Date(row.created_at).toLocaleString('vi-VN')}</time></td>
                        <td>{row.duration_ms === null ? '—' : `${(row.duration_ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} giây`}</td>
                        <td className={row.status === 'COMPLETED' ? styles.success : ['FAILED', 'NOT_COMPARABLE'].includes(row.status) ? styles.error : styles.pending}>{statuses[row.status]}</td>
                        <td><Link className={layout.secondaryButton} aria-current={row.id === selectedId ? 'page' : undefined}
                            to={`/experiments/results?run_id=${row.id}`}>Xem kết quả</Link></td>
                    </tr>)}</tbody>
                </table></div>}
            {data.total_pages > 1 && <div className={styles.row}>
                <button className={layout.secondaryButton} disabled={busy || page <= 1} onClick={() => setPage(n => n - 1)}>Trước</button>
                <span>Trang {data.page}/{data.total_pages} · {data.total} thực nghiệm</span>
                <button className={layout.primaryButton} disabled={busy || page >= data.total_pages} onClick={() => setPage(n => n + 1)}>Tiếp</button>
            </div>}
        </>}
    </section>
}

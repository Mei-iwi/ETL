import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { getEvaluationReport, getEvaluationRun, recentEvaluationId } from '../../services/evaluationApi'
import type { EvaluationRun } from '../../types/evaluation'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'
import { number, visibleWarnings, warning } from './evaluationHelpers'
import { ExperimentPageHeader } from './ExperimentPageHeader'
import { EvaluationHistory } from './EvaluationHistory'

export function EvaluationRunView() {
    const [params] = useSearchParams()
    const id = params.get('run_id') || ''
    return <section className={`${layout.page} ${styles.page}`}>
        <ExperimentPageHeader title="Kết quả và báo cáo" />
        <div className={styles.stack}>
            <EvaluationHistory selectedId={id} />
            {id && <RunView key={id} id={id} />}
        </div>
    </section>
}

function RunView({ id }: { id: string }) {
    const [data, setData] = useState<EvaluationRun | null>(null)
    const [error, setError] = useState('')
    const [detailsOpen, setDetailsOpen] = useState(false)
    const [attempt, setAttempt] = useState(0)
    const [downloading, setDownloading] = useState(false)
    const [downloadError, setDownloadError] = useState('')
    const displayedWarnings = visibleWarnings(data?.warnings ?? [])
    const downloadController = useRef<AbortController | null>(null)
    const downloadBusy = useRef(false)
    useEffect(() => () => downloadController.current?.abort(), [])
    useEffect(() => {
        if (!id) return
        const controller = new AbortController()
        void Promise.resolve().then(() => controller.signal.aborted ? null : getEvaluationRun(id, 0, controller.signal, false))
            .then(value => { if (value && !controller.signal.aborted) { setData(value); setError(''); recentEvaluationId('run', id) } })
            .catch(reason => { if (!controller.signal.aborted) { setData(null); setError(reason instanceof Error ? reason.message : 'Không tải được kết quả.') } })
        return () => controller.abort()
    }, [id, attempt])
    async function download(format: 'csv' | 'json') {
        if (downloadBusy.current) return
        downloadBusy.current = true
        const controller = new AbortController(); downloadController.current = controller
        setDownloading(true); setDownloadError('')
        try {
            const file = await getEvaluationReport(id, format, controller.signal)
            if (controller.signal.aborted) return
            const url = URL.createObjectURL(file.blob)
            const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.filename
            document.body.append(anchor); anchor.click(); anchor.remove()
            window.setTimeout(() => URL.revokeObjectURL(url), 1000)
        } catch (reason) { if (!controller.signal.aborted) setDownloadError(reason instanceof Error ? reason.message : 'Không tải được báo cáo.') }
        finally { downloadBusy.current = false; if (!controller.signal.aborted) setDownloading(false) }
    }
    return <>
        <div className={styles.stack}>
            {error && <div role="alert" className={styles.error}>{error} <button className={layout.secondaryButton} onClick={() => setAttempt(n => n + 1)}>Thử lại</button></div>}
            {id && !error && !data && <div role="status" className={styles.pending}>Đang tải kết quả…</div>}
            {data && <>
                <section className={`${layout.card} ${styles.card}`}>
                    <h2>{data.dataset_name}</h2><div className={data.status === 'COMPLETED' ? styles.success : ['FAILED', 'NOT_COMPARABLE'].includes(data.status) ? styles.error : styles.pending}>Trạng thái: {data.status}</div>
                    <div>{data.queries.length} truy vấn · {data.queries.reduce((count, query) => count + query.relevance.filter(label => label.grade >= data.relevance_threshold).length, 0)} nhãn dương · Đơn vị: học liệu.</div>
                    <details><summary>Thông tin lần chạy</summary>
                        <div className={styles.identifier}>Run ID: {data.id}</div>
                        <div className={styles.identifier}>Dataset hash: {data.dataset_hash}</div>
                        <div className={styles.identifier}>Corpus: {data.corpus_fingerprint}</div>
                        <div className={styles.identifier}>Model: {data.snapshot.model.id} · {data.snapshot.model.version} · {data.snapshot.model.dimension} chiều</div>
                        <div className={styles.muted}>{new Date(data.created_at).toLocaleString('vi-VN')}</div>
                    </details>
                    {displayedWarnings.length > 0 && <details><summary>Lưu ý ({displayedWarnings.length})</summary>
                        {displayedWarnings.map(code => <div className={code.includes('CHANGED') || code.includes('TIMEOUT') ? styles.error : styles.pending} key={code}>{warning(code)}</div>)}
                    </details>}
                    <div className={styles.row}>
                        <Link className={layout.secondaryButton} to={`/experiments/run?dataset_id=${data.dataset_id}`}>Chạy lại</Link>
                    </div>
                </section>
                <section className={`${layout.card} ${styles.card}`}>
                    <h2>So sánh phương pháp</h2>
                    <div className={styles.row}><button className={layout.primaryButton} disabled={downloading || data.status !== 'COMPLETED'} onClick={() => void download('csv')}>Xuất báo cáo CSV</button>
                        <button className={layout.secondaryButton} disabled={downloading || data.status !== 'COMPLETED'} onClick={() => void download('json')}>Tải JSON</button></div>
                    {data.status !== 'COMPLETED' && <div className={styles.pending}>Chỉ xuất báo cáo khi hoàn tất.</div>}
                    {downloadError && <div role="alert" className={styles.error}>{downloadError}</div>}
                    <div className={styles.tableWrap} role="region" aria-label="Bảng so sánh phương pháp" tabIndex={0}><table className={styles.table}>
                        <thead><tr><th>Phương pháp</th><th>k</th><th>Precision@k</th><th>Recall@k</th><th>MRR@k</th><th>nDCG@k</th><th>TB ms</th><th>P50 ms</th><th>P95 ms</th><th>Thành công/Lỗi</th><th>Recall hợp lệ/loại</th></tr></thead>
                        <tbody>{Object.entries(data.summary).flatMap(([method, summary]) => summary ? Object.entries(summary.metrics).map(([k, cells]) => <tr key={`${method}:${k}`}>
                            <td>{method.toUpperCase()}</td><td>{k}</td><td>{number(cells.precision.value)}</td><td>{number(cells.recall.value)}</td>
                            <td>{number(cells.mrr.value)}</td><td>{number(cells.ndcg.value)}</td><td>{number(summary.latency_ms.mean)}</td><td>{number(summary.latency_ms.p50)}</td><td>{number(summary.latency_ms.p95)}</td>
                            <td>{summary.successful_queries}/{summary.failed_queries}</td><td>{cells.recall.valid_queries}/{cells.recall.excluded_queries}</td></tr>) : [])}</tbody>
                    </table></div>
                    <details><summary>Cách đọc chỉ số</summary><div className={styles.muted}>Chất lượng càng cao càng tốt; thời gian càng thấp càng tốt. Precision chia k kể cả thiếu kết quả; grade ≥ 1 là liên quan. n/a không phải 0. Recall/MRR/nDCG loại truy vấn không có nhãn dương khỏi trung bình. Điểm RRF không so trực tiếp với cosine.</div></details>
                </section>
                <details className={`${layout.card} ${styles.card} ${styles.queryDetails}`} onToggle={event => setDetailsOpen(event.currentTarget.open)}>
                    <summary aria-expanded={detailsOpen} aria-controls="evaluation-observations">Chi tiết theo truy vấn</summary>
                    {detailsOpen && <RunObservations key={id} id={id} fingerprint={data.corpus_fingerprint} />}
                </details>
            </>}
        </div>
    </>
}

function RunObservations({ id, fingerprint }: { id: string; fingerprint: string }) {
    const [offset, setOffset] = useState(0)
    const [attempt, setAttempt] = useState(0)
    const [loaded, setLoaded] = useState<{ offset: number; data: EvaluationRun } | null>(null)
    const [error, setError] = useState<{ offset: number; message: string } | null>(null)
    useEffect(() => {
        const controller = new AbortController()
        void Promise.resolve().then(() => controller.signal.aborted ? null : getEvaluationRun(id, offset, controller.signal))
            .then(data => {
                if (data && !controller.signal.aborted) {
                    if (data.id !== id || data.corpus_fingerprint !== fingerprint) throw new Error('Kết quả đã thay đổi. Mở lại lần chạy để cập nhật.')
                    setLoaded({ offset, data }); setError(null)
                }
            })
            .catch(reason => { if (!controller.signal.aborted) setError({ offset, message: reason instanceof Error ? reason.message : 'Không tải được chi tiết.' }) })
        return () => controller.abort()
    }, [id, offset, attempt, fingerprint])
    const data = loaded?.offset === offset ? loaded.data : null
    const message = error?.offset === offset ? error.message : ''
    return <div id="evaluation-observations" aria-busy={!data && !message}>
        {message ? <p role="alert">{message} <button type="button" onClick={() => { setError(null); setAttempt(value => value + 1) }}>Thử lại</button></p>
            : !data ? <p role="status">Đang tải chi tiết…</p> : <>
            {data.observations.map(row => <details key={`${row.query_id}:${row.method}`} className={styles.query}>
                <summary>{row.query_id} · {row.method.toUpperCase()} · {row.error_code || `${number(row.latency_ms)} ms`}</summary>
                <div>{data.queries.find(query => query.query_id === row.query_id)?.query}</div>
                <div className={styles.identifier}>Nhãn: {data.queries.find(query => query.query_id === row.query_id)?.relevance.map(label => `${label.item_id}:${label.grade}`).join(', ')}</div>
                <ol>{row.ranked.map(item => <li className={styles.identifier} key={item.resource_id}>{item.resource_id} · {item.resource_version_id} · Điểm {number(item.score)}</li>)}</ol>
            </details>)}
            <div className={styles.row}>
                <button type="button" disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>Trước</button>
                <span>Trang {Math.floor(offset / 20) + 1} / {Math.max(1, Math.ceil(data.pagination.total / 20))}</span>
                <button type="button" disabled={offset + 20 >= data.pagination.total} onClick={() => setOffset(value => value + 20)}>Tiếp</button>
            </div>
        </>}
    </div>
}

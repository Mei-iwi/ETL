import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { getEvaluationReport, getEvaluationRun, recentEvaluationId } from '../../services/evaluationApi'
import type { EvaluationRun } from '../../types/evaluation'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'
import { number, warning } from './evaluationHelpers'

export function EvaluationRunView({ report = false }: { report?: boolean }) {
    const [params] = useSearchParams()
    const id = params.get('run_id') || ''
    return <RunView key={`${id}:${report}`} id={id} report={report} />
}

function RunView({ id, report }: { id: string; report: boolean }) {
    const [, setParams] = useSearchParams()
    const [input, setInput] = useState(id || recentEvaluationId('run'))
    const [data, setData] = useState<EvaluationRun | null>(null)
    const [error, setError] = useState('')
    const [offset, setOffset] = useState(0)
    const [attempt, setAttempt] = useState(0)
    const [downloading, setDownloading] = useState(false)
    const [downloadError, setDownloadError] = useState('')
    const downloadController = useRef<AbortController | null>(null)
    const downloadBusy = useRef(false)
    useEffect(() => () => downloadController.current?.abort(), [])
    useEffect(() => {
        if (!id) return
        const controller = new AbortController()
        void Promise.resolve().then(() => controller.signal.aborted ? null : getEvaluationRun(id, offset, controller.signal))
            .then(value => { if (value && !controller.signal.aborted) { setData(value); setError(''); recentEvaluationId('run', id) } })
            .catch(reason => { if (!controller.signal.aborted) { setData(null); setError(reason instanceof Error ? reason.message : 'Không tải được kết quả.') } })
        return () => controller.abort()
    }, [id, offset, attempt])
    function open(event: FormEvent) { event.preventDefault(); setParams({ run_id: input.trim() }); setAttempt(n => n + 1) }
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
    return <section className={layout.page}>
        <header className={layout.pageHeader}><h1>{report ? 'Báo cáo thực nghiệm' : 'Kết quả đánh giá'}</h1></header>
        <div className={styles.stack}>
            <form className={`${layout.card} ${layout.form}`} onSubmit={open}>
                <label className={layout.field}>Run ID<input value={input} required onChange={e => setInput(e.target.value)} /></label>
                <button className={layout.primaryButton}>Xem kết quả</button>
            </form>
            {!id && <p className={styles.muted}>Nhập Run ID đã lưu để mở kết quả. Trình duyệt chỉ nhớ ID gần nhất.</p>}
            {error && <p role="alert" className={styles.error}>{error}</p>}
            {id && !error && !data && <p role="status" className={styles.pending}>Đang tải kết quả…</p>}
            {data && <>
                <section className={layout.card}>
                    <h2>{data.dataset_name}</h2><p className={data.status === 'COMPLETED' ? styles.success : ['FAILED', 'NOT_COMPARABLE'].includes(data.status) ? styles.error : styles.pending}>Trạng thái: {data.status}</p>
                    <p>{data.queries.length} truy vấn · {data.queries.reduce((count, query) => count + query.relevance.filter(label => label.grade >= data.relevance_threshold).length, 0)} nhãn dương · Đơn vị: học liệu.</p>
                    <p className={styles.identifier}>Run ID: {data.id}</p>
                    <details><summary>Thông tin tái lập</summary>
                        <p className={styles.identifier}>Dataset hash: {data.dataset_hash}</p>
                        <p className={styles.identifier}>Corpus: {data.corpus_fingerprint}</p>
                        <p className={styles.identifier}>Model: {data.snapshot.model.id} · {data.snapshot.model.version} · {data.snapshot.model.dimension} chiều</p>
                        <p className={styles.muted}>{new Date(data.created_at).toLocaleString('vi-VN')}</p>
                    </details>
                    {data.warnings.map(code => <p className={styles.muted} key={code}>{warning(code)}</p>)}
                    <div className={styles.row}>
                        <Link className={layout.secondaryButton} to={`/experiments/${report ? 'results' : 'reports'}?run_id=${id}`}>{report ? 'Kết quả đánh giá' : 'Xuất báo cáo'}</Link>
                        <Link className={layout.secondaryButton} to={`/experiments/run?dataset_id=${data.dataset_id}`}>Chạy lại</Link>
                    </div>
                </section>
                {report ? <section className={layout.card}>
                    <p className={styles.muted}>CSV UTF-8 BOM dùng cho Excel; JSON chứa kết quả chi tiết đã lưu. Chỉ xuất run COMPLETED.</p>
                    <div className={styles.row}><button className={layout.primaryButton} disabled={downloading || data.status !== 'COMPLETED'} onClick={() => void download('csv')}>Xuất báo cáo CSV</button>
                        <button className={layout.secondaryButton} disabled={downloading || data.status !== 'COMPLETED'} onClick={() => void download('json')}>Tải JSON</button></div>
                    {downloadError && <p role="alert" className={styles.error}>{downloadError}</p>}
                </section> : <>
                    <section className={layout.card}>
                        <h2>So sánh phương pháp</h2><div className={styles.tableWrap}><table className={styles.table}>
                            <thead><tr><th>Phương pháp</th><th>k</th><th>Precision@k</th><th>Recall@k</th><th>MRR@k</th><th>nDCG@k</th><th>TB ms</th><th>P50 ms</th><th>P95 ms</th><th>Thành công/Lỗi</th><th>Recall hợp lệ/loại</th></tr></thead>
                            <tbody>{Object.entries(data.summary).flatMap(([method, summary]) => summary ? Object.entries(summary.metrics).map(([k, cells]) => <tr key={`${method}:${k}`}>
                                <td>{method.toUpperCase()}</td><td>{k}</td><td>{number(cells.precision.value)}</td><td>{number(cells.recall.value)}</td>
                                <td>{number(cells.mrr.value)}</td><td>{number(cells.ndcg.value)}</td><td>{number(summary.latency_ms.mean)}</td><td>{number(summary.latency_ms.p50)}</td><td>{number(summary.latency_ms.p95)}</td>
                                <td>{summary.successful_queries}/{summary.failed_queries}</td><td>{cells.recall.valid_queries}/{cells.recall.excluded_queries}</td></tr>) : [])}</tbody>
                        </table></div>
                        <p className={styles.muted}>Chất lượng càng cao càng tốt; latency càng thấp càng tốt. Precision chia k kể cả thiếu kết quả; grade ≥ 1 là liên quan. n/a: không xác định/không đo được, không phải 0. Macro-average; Recall/MRR/nDCG loại truy vấn không có nhãn dương. Điểm RRF khác thang cosine, không so trực tiếp.</p>
                    </section>
                    <section className={layout.card}><h2>Chi tiết theo truy vấn</h2>
                        {data.observations.map(row => <details key={`${row.query_id}:${row.method}`} className={styles.query}>
                            <summary>{row.query_id} · {row.method.toUpperCase()} · {row.error_code || `${number(row.latency_ms)} ms`}</summary>
                            <p>{data.queries.find(query => query.query_id === row.query_id)?.query}</p>
                            <p className={styles.identifier}>Nhãn: {data.queries.find(query => query.query_id === row.query_id)?.relevance.map(label => `${label.item_id}:${label.grade}`).join(', ')}</p>
                            <ol>{row.ranked.map(item => <li className={styles.identifier} key={item.resource_id}>{item.resource_id} · {item.resource_version_id} · Điểm {number(item.score)}</li>)}</ol>
                        </details>)}
                        <div className={styles.row}><button className={layout.secondaryButton} disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 20))}>Trước</button>
                            <span>{Math.min(offset + 1, data.pagination.total)}–{Math.min(offset + 20, data.pagination.total)}/{data.pagination.total}</span>
                            <button className={layout.primaryButton} disabled={offset + 20 >= data.pagination.total} onClick={() => setOffset(n => n + 20)}>Tiếp</button></div>
                    </section>
                </>}
            </>}
        </div>
    </section>
}

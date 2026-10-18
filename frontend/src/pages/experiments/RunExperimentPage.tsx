import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { recentEvaluationId, runEvaluation } from '../../services/evaluationApi'
import type { EvaluationProgress } from '../../types/evaluation'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'
import { ExperimentPageHeader } from './ExperimentPageHeader'

export function RunExperimentPage() {
    const [params] = useSearchParams()
    const [dataset, setDataset] = useState(() => params.get('dataset_id') || recentEvaluationId('dataset'))
    const [kValues, setKValues] = useState('1,3,5')
    const [maxResults, setMaxResults] = useState(10)
    const [warmup, setWarmup] = useState(0)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const [progress, setProgress] = useState<EvaluationProgress | null>(null)
    const inFlight = useRef(false)
    const controller = useRef<AbortController | null>(null)
    const navigate = useNavigate()
    useEffect(() => () => controller.current?.abort(), [])
    async function submit(event: FormEvent) {
        event.preventDefault()
        if (inFlight.current) return
        setError(''); setProgress(null)
        const ks = kValues.split(',').map(value => Number(value.trim()))
        if (!ks.length || ks.some(k => !Number.isInteger(k) || k < 1 || k > maxResults) || new Set(ks).size !== ks.length) { setError('Các k cần khác nhau, từ 1 đến số kết quả tối đa.'); return }
        inFlight.current = true; setBusy(true)
        const current = new AbortController(); controller.current = current
        try {
            const result = await runEvaluation({
                dataset_id: dataset.trim(), methods: ['bow', 'vector', 'hybrid'],
                k_values: ks.sort((a, b) => a - b), max_results: maxResults, warmup_queries: warmup
            }, current.signal, value => {
                if (!current.signal.aborted) { setProgress(value); if (value.run_id) recentEvaluationId('run', value.run_id) }
            })
            if (!current.signal.aborted) { recentEvaluationId('run', result.run_id); navigate(`/experiments/results?run_id=${result.run_id}`) }
        } catch (reason) { if (!current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không chạy được thực nghiệm.') }
        finally { inFlight.current = false; if (!current.signal.aborted) setBusy(false) }
    }
    return <section className={`${layout.page} ${styles.page}`}>
        <ExperimentPageHeader title="Chạy thực nghiệm" />
        <form className={`${layout.card} ${layout.form} ${styles.card}`} onSubmit={event => void submit(event)}>
            <fieldset className={styles.controls} disabled={busy}>
                <div className={styles.sectionHeading}><h2>Cấu hình thực nghiệm</h2></div>
                <label className={layout.field}>Dataset ID<input value={dataset} required placeholder="Nhập ID bộ dữ liệu đã tạo" onChange={e => setDataset(e.target.value)} /></label>
                <div className={styles.methodPanel}><span className={styles.fieldLabel}>Phương pháp đánh giá</span><div className={styles.methods}><span>BoW</span><span>Vector</span><span>Hybrid</span></div></div>
                <div className={styles.row}>
                    <label className={layout.field}>Giá trị k<input value={kValues} required aria-describedby="k-help" onChange={e => setKValues(e.target.value)} /><span id="k-help" className={styles.muted}>Phân cách bằng dấu phẩy, ví dụ: 1,3,5.</span></label>
                    <label className={layout.field}>Kết quả tối đa<input type="number" min={1} max={20} value={maxResults} required aria-describedby="max-results-help" onChange={e => setMaxResults(Number(e.target.value))} /><span id="max-results-help" className={styles.muted}>Từ 1 đến 20 kết quả cho mỗi truy vấn.</span></label>
                </div>
                <details className={styles.disclosure}><summary>Tùy chọn nâng cao</summary><label className={layout.field}>Số lượt làm nóng mỗi phương pháp<input type="number" min={0} max={3} value={warmup} required onChange={e => setWarmup(Number(e.target.value))} /></label></details>
            </fieldset>
            {error && <div role="alert" className={styles.error}>{error}</div>}
            {busy && <div role="status" aria-live="polite" className={styles.progressArea}>
                <label htmlFor="evaluation-progress" className={styles.pending}>{progress ? `${progress.percent}% · ${progress.completed}/${progress.total} lượt${progress.phase === 'warmup' ? ' · Làm nóng' : progress.completed === progress.total && progress.percent < 100 ? ' · Đang lưu kết quả' : ''}` : 'Đang kiểm tra dữ liệu…'}</label>
                <progress id="evaluation-progress" className={styles.progress} max={100} value={progress?.percent} aria-label="Tiến trình thực nghiệm" />
                <div>Rời trang sẽ ngừng chờ; máy chủ có thể vẫn đang xử lý.</div>
            </div>}
            {!busy && error && progress?.run_id && <Link className={layout.secondaryButton} to={`/experiments/results?run_id=${progress.run_id}`}>Kiểm tra kết quả lần chạy</Link>}
            <div className={styles.formFooter}>
                {!busy && <Link className={layout.secondaryButton} to="/experiments/evaluation-data">Dữ liệu đánh giá</Link>}
                <button className={layout.primaryButton} disabled={busy}>{busy ? 'Đang thực nghiệm…' : 'Chạy thực nghiệm'}</button></div>
        </form>
    </section>
}

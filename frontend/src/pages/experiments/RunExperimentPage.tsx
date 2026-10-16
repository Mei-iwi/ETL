import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { recentEvaluationId, runEvaluation } from '../../services/evaluationApi'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'

export function RunExperimentPage() {
    const [params] = useSearchParams()
    const [dataset, setDataset] = useState(() => params.get('dataset_id') || recentEvaluationId('dataset'))
    const [kValues, setKValues] = useState('1,3,5')
    const [maxResults, setMaxResults] = useState(10)
    const [warmup, setWarmup] = useState(0)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const inFlight = useRef(false)
    const controller = useRef<AbortController | null>(null)
    const navigate = useNavigate()
    useEffect(() => () => controller.current?.abort(), [])
    async function submit(event: FormEvent) {
        event.preventDefault()
        if (inFlight.current) return
        setError('')
        const ks = kValues.split(',').map(value => Number(value.trim()))
        if (!ks.length || ks.some(k => !Number.isInteger(k) || k < 1 || k > maxResults) || new Set(ks).size !== ks.length) { setError('Các k cần khác nhau, từ 1 đến số kết quả tối đa.'); return }
        inFlight.current = true; setBusy(true)
        const current = new AbortController(); controller.current = current
        try {
            const result = await runEvaluation({ dataset_id: dataset.trim(), methods: ['bow', 'vector', 'hybrid'],
                k_values: ks.sort((a, b) => a - b), max_results: maxResults, warmup_queries: warmup }, current.signal)
            if (!current.signal.aborted) { recentEvaluationId('run', result.run_id); navigate(`/experiments/results?run_id=${result.run_id}`) }
        } catch (reason) { if (!current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không chạy được thực nghiệm.') }
        finally { inFlight.current = false; if (!current.signal.aborted) setBusy(false) }
    }
    return <section className={layout.page}><header className={layout.pageHeader}><h1>Chạy thực nghiệm</h1></header>
        <form className={`${layout.card} ${layout.form}`} onSubmit={event => void submit(event)}>
            <label className={layout.field}>Dataset ID<input value={dataset} required onChange={e => setDataset(e.target.value)} /></label>
            <p>BoW · Vector · Hybrid</p><p className={styles.muted}>Cùng corpus, policy và bộ nhãn. Nếu chỉ mục thay đổi, kết quả không được coi là so sánh hợp lệ.</p>
            <div className={styles.row}>
                <label>Giá trị k<input value={kValues} required onChange={e => setKValues(e.target.value)} /></label>
                <label>Kết quả tối đa<input type="number" min={1} max={20} value={maxResults} required onChange={e => setMaxResults(Number(e.target.value))} /></label>
                <label>Warmup mỗi phương pháp<input type="number" min={0} max={3} value={warmup} required onChange={e => setWarmup(Number(e.target.value))} /></label>
            </div>
            <p className={styles.muted}>Chỉ nhớ ID gần nhất trên trình duyệt. Có thể nhập ID dataset đã lưu; không có API liệt kê toàn bộ dataset.</p>
            {error && <p role="alert" className={styles.error}>{error}</p>}
            {busy && <p role="status" className={styles.pending}>Đang chạy ba phương pháp… Không đóng trang hoặc gửi lại yêu cầu.</p>}
            <button className={layout.primaryButton} disabled={busy}>{busy ? 'Đang thực nghiệm…' : 'Chạy thực nghiệm'}</button>
        </form>
    </section>
}

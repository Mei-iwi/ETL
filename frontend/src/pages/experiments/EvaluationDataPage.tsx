import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { createDataset, recentEvaluationId } from '../../services/evaluationApi'
import type { DatasetCreated } from '../../types/evaluation'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'
import { blankQuery, datasetInput, importDraft, setDraftGrade, visibleWarnings, warning } from './evaluationHelpers'

export function EvaluationDataPage() {
    const [name, setName] = useState('')
    const [description, setDescription] = useState('')
    const [queries, setQueries] = useState([blankQuery(1)])
    const [result, setResult] = useState<DatasetCreated | null>(null)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const [copied, setCopied] = useState(false)
    const controller = useRef<AbortController | null>(null)
    const inFlight = useRef(false)
    useEffect(() => () => controller.current?.abort(), [])
    async function submit(event: FormEvent) {
        event.preventDefault(); setError('')
        if (inFlight.current) return
        inFlight.current = true; setBusy(true)
        const current = new AbortController(); controller.current = current
        try {
            const data = await createDataset(datasetInput(name, description, queries), current.signal)
            if (current.signal.aborted) return
            setResult(data); recentEvaluationId('dataset', data.dataset_id)
        } catch (reason) { if (!current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không lưu được dataset.') }
        finally { inFlight.current = false; if (!current.signal.aborted) setBusy(false) }
    }
    async function loadFile(file?: File) {
        if (!file) return
        setError(''); setResult(null)
        try {
            if (file.size > 1024 * 1024) throw new Error('File JSON tối đa 1 MB.')
            const draft = importDraft(JSON.parse(await file.text()))
            setName(draft.name); setDescription(draft.description); setQueries(draft.queries)
        } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không đọc được JSON.') }
    }
    return <section className={layout.page}>
        <header className={layout.pageHeader}><h1>Dữ liệu đánh giá</h1></header>
        <div className={styles.stack}>
            <form onSubmit={event => void submit(event)} className={`${layout.card} ${layout.form}`}>
                <label className={layout.field}>Tên dataset<input value={name} maxLength={150} required onChange={e => setName(e.target.value)} /></label>
                <details><summary>Mô tả (tùy chọn)</summary><label className={layout.field}>Mô tả<textarea className={styles.textarea} value={description} maxLength={1000} onChange={e => setDescription(e.target.value)} /></label></details>
                <label className={layout.field}>Nhập JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={e => { void loadFile(e.target.files?.[0]); e.target.value = '' }} /></label>
                <p className={styles.compactNote}>{queries.length} truy vấn · Nhãn 0–3 · Từ 1 là liên quan</p>
                {queries.map((row, index) => <details className={styles.query} key={index} open={index === 0}>
                    <summary>{row.query_id || `Truy vấn ${index + 1}`} · {row.query || 'Truy vấn mới'}{row.candidates ? ` · ${row.candidates.filter(item => item.grade !== null).length}/${row.candidates.length} đã chấm` : ''}</summary>
                    <div className={styles.stack}>
                        <label>Mã truy vấn<input aria-label={`Mã truy vấn ${index + 1}`} value={row.query_id} maxLength={50}
                            onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, query_id: e.target.value } : item))} /></label>
                        <label>Nội dung<input aria-label={`Nội dung truy vấn ${index + 1}`} value={row.query} maxLength={300} required
                            onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, query: e.target.value } : item))} /></label>
                        {row.candidates ? <>
                            <p className={styles.muted}>Đã chấm {row.candidates.filter(item => item.grade !== null).length}/{row.candidates.length} học liệu</p>
                            {row.candidates.map(item => <div className={styles.query} key={item.id}>
                                <label className={layout.field}>{item.title ? `${item.title} (${item.id})` : item.id}
                                    <select aria-label={`Điểm ${row.query_id} ${item.id}`} value={item.grade ?? ''}
                                        onChange={e => { const grade = e.target.value === '' ? null : Number(e.target.value); setQueries(rows => rows.map((entry, i) => i === index ? setDraftGrade(entry, item.id, grade) : entry)) }}>
                                        <option value="">Chưa chấm</option><option value="0">0 — Không liên quan</option><option value="1">1 — Ít liên quan</option><option value="2">2 — Liên quan</option><option value="3">3 — Rất liên quan</option>
                                    </select>
                                </label>
                                {item.description && <details><summary>Nội dung ứng viên</summary><p>{item.description}</p></details>}
                            </div>)}
                        </> : <label>Nhãn liên quan<textarea aria-label={`Nhãn truy vấn ${index + 1}`} className={styles.textarea} value={row.labels} placeholder="resource_id:grade — mỗi dòng một nhãn"
                            onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, labels: e.target.value } : item))} /></label>}
                        <button type="button" className={layout.secondaryButton} disabled={queries.length === 1} onClick={() => setQueries(rows => rows.filter((_, i) => i !== index))}>Xóa truy vấn</button>
                    </div>
                </details>)}
                <button type="button" className={layout.secondaryButton} disabled={queries.length >= 20} onClick={() => setQueries(rows => [...rows, blankQuery(rows.length + 1, rows)])}>Thêm truy vấn</button>
                {error && <p role="alert" className={styles.error}>{error}</p>}
                <button className={layout.primaryButton} disabled={busy}>{busy ? 'Đang lưu…' : 'Tạo dataset'}</button>
            </form>
            {result && <div className={layout.card}>
                <p className={styles.success}>Đã lưu {result.number_of_queries} truy vấn · {result.relevant_items} nhãn dương.</p>
                <p className={styles.identifier}>Dataset ID: {result.dataset_id}</p>
                <div className={styles.row}><button className={layout.secondaryButton} onClick={() => { void navigator.clipboard.writeText(result.dataset_id).then(() => setCopied(true)).catch(() => setError('Không sao chép được ID.')) }}>{copied ? 'Đã sao chép' : 'Sao chép ID'}</button>
                    <Link className={layout.primaryButton} to={`/experiments/run?dataset_id=${result.dataset_id}`}>Chạy thực nghiệm</Link></div>
                {visibleWarnings(result.validation.warnings).map(code => <p className={styles.muted} key={code}>{warning(code)}</p>)}
            </div>}
        </div>
    </section>
}

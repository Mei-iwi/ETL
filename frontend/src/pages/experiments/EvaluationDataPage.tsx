import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { createDataset, recentEvaluationId } from '../../services/evaluationApi'
import type { DatasetCreated, DatasetInput } from '../../types/evaluation'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'
import { blankQuery, datasetInput, importDraft, warning } from './evaluationHelpers'

export function EvaluationDataPage() {
    const [name, setName] = useState('')
    const [description, setDescription] = useState('')
    const [queries, setQueries] = useState([blankQuery(1)])
    const [confirmed, setConfirmed] = useState(false)
    const [pending, setPending] = useState<DatasetInput | null>(null)
    const [result, setResult] = useState<DatasetCreated | null>(null)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const [copied, setCopied] = useState(false)
    const controller = useRef<AbortController | null>(null)
    const inFlight = useRef(false)
    useEffect(() => () => controller.current?.abort(), [])
    function submit(event: FormEvent) {
        event.preventDefault(); setError('')
        try { setPending(datasetInput(name, description, queries, confirmed)) }
        catch (reason) { setError(reason instanceof Error ? reason.message : 'Dữ liệu không hợp lệ.') }
    }
    async function save() {
        if (!pending || inFlight.current) return
        inFlight.current = true; setBusy(true)
        const current = new AbortController(); controller.current = current
        try {
            const data = await createDataset(pending, current.signal)
            if (current.signal.aborted) return
            setResult(data); recentEvaluationId('dataset', data.dataset_id); setPending(null)
        } catch (reason) { if (!current.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không lưu được dataset.') }
        finally { inFlight.current = false; if (!current.signal.aborted) setBusy(false) }
    }
    async function loadFile(file?: File) {
        if (!file) return
        setError(''); setResult(null); setConfirmed(false)
        try {
            if (file.size > 1024 * 1024) throw new Error('File JSON tối đa 1 MB.')
            const draft = importDraft(JSON.parse(await file.text()))
            setName(draft.name); setDescription(draft.description); setQueries(draft.queries)
        } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không đọc được JSON.') }
    }
    return <section className={layout.page}>
        <header className={layout.pageHeader}><h1>Dữ liệu đánh giá</h1></header>
        <div className={styles.stack}>
            <form onSubmit={submit} className={`${layout.card} ${layout.form}`}>
                <label className={layout.field}>Tên dataset<input value={name} maxLength={150} required onChange={e => setName(e.target.value)} /></label>
                <label className={layout.field}>Mô tả<textarea className={styles.textarea} value={description} maxLength={1000} onChange={e => setDescription(e.target.value)} /></label>
                <label className={layout.field}>Nhập JSON<input type="file" accept=".json,application/json" onChange={e => void loadFile(e.target.files?.[0])} /></label>
                <p className={styles.muted}>Đánh giá theo resource_id (học liệu), không phải Chunk/ContentBag. Nhãn 0: không liên quan; 1: ít; 2: liên quan; 3: rất liên quan. Từ 1 là nhãn dương. Không dùng điểm Search làm nhãn.</p>
                {queries.map((row, index) => <fieldset className={styles.query} key={index}>
                    <legend>Truy vấn {index + 1}</legend>
                    <label>Mã truy vấn<input aria-label={`Mã truy vấn ${index + 1}`} value={row.query_id} maxLength={50}
                        onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, query_id: e.target.value } : item))} /></label>
                    <label>Nội dung<input aria-label={`Nội dung truy vấn ${index + 1}`} value={row.query} maxLength={300} required
                        onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, query: e.target.value } : item))} /></label>
                    <label>Nhãn liên quan<textarea aria-label={`Nhãn truy vấn ${index + 1}`} className={styles.textarea} value={row.labels} placeholder="resource_id:grade — mỗi dòng một nhãn"
                        onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, labels: e.target.value } : item))} /></label>
                    <button type="button" className={layout.secondaryButton} disabled={queries.length === 1} onClick={() => setQueries(rows => rows.filter((_, i) => i !== index))}>Xóa truy vấn</button>
                </fieldset>)}
                <button type="button" className={layout.secondaryButton} disabled={queries.length >= 20} onClick={() => setQueries(rows => [...rows, blankQuery(rows.length + 1)])}>Thêm truy vấn</button>
                <label className={styles.check}><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />Tôi xác nhận toàn bộ nhãn đã được con người đánh giá, không suy ra từ thứ hạng Search.</label>
                {error && <p role="alert" className={styles.error}>{error}</p>}
                <button className={layout.primaryButton} disabled={busy}>Tạo dataset</button>
            </form>
            {result && <div className={layout.card}>
                <p className={styles.success}>Đã lưu {result.number_of_queries} truy vấn · {result.relevant_items} nhãn dương.</p>
                <p className={styles.identifier}>Dataset ID: {result.dataset_id}</p>
                <div className={styles.row}><button className={layout.secondaryButton} onClick={() => { void navigator.clipboard.writeText(result.dataset_id).then(() => setCopied(true)).catch(() => setError('Không sao chép được ID.')) }}>{copied ? 'Đã sao chép' : 'Sao chép ID'}</button>
                    <Link className={layout.primaryButton} to={`/experiments/run?dataset_id=${result.dataset_id}`}>Chạy thực nghiệm</Link></div>
                {result.validation.warnings.map(code => <p className={styles.muted} key={code}>{warning(code)}</p>)}
            </div>}
        </div>
        {pending && <div className={styles.modal} role="presentation"><section className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="dataset-confirm">
            <h2 id="dataset-confirm">Lưu bộ nhãn đã xác nhận?</h2><p>{pending.queries.length} truy vấn · Đơn vị đánh giá: học liệu.</p>
            {error && <p role="alert" className={styles.error}>{error}</p>}
            <div className={styles.row}><button className={layout.primaryButton} disabled={busy} onClick={() => void save()}>{busy ? 'Đang lưu…' : 'Xác nhận'}</button>
                <button className={layout.secondaryButton} disabled={busy} onClick={() => setPending(null)}>Hủy</button></div>
        </section></div>}
    </section>
}

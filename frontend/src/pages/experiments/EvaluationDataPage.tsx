import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { FileJson, Upload, X } from 'lucide-react'
import { createDataset, recentEvaluationId } from '../../services/evaluationApi'
import type { DatasetCreated } from '../../types/evaluation'
import layout from '../library/LibraryPages.module.css'
import styles from './ExperimentPages.module.css'
import { blankQuery, datasetInput, importDraft, setDraftGrade, visibleWarnings, warning } from './evaluationHelpers'
import { ExperimentPageHeader } from './ExperimentPageHeader'
import upload from '../library/LibraryCreatePages.module.css'

export function EvaluationDataPage() {
    const [name, setName] = useState('')
    const [description, setDescription] = useState('')
    const [queries, setQueries] = useState([blankQuery(1)])
    const [result, setResult] = useState<DatasetCreated | null>(null)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)
    const [copied, setCopied] = useState(false)
    const [selectedFile, setSelectedFile] = useState<File | null>(null)
    const [dragging, setDragging] = useState(false)
    const [importing, setImporting] = useState(false)
    const fileInput = useRef<HTMLInputElement | null>(null)
    const readingFile = useRef(false)
    const controller = useRef<AbortController | null>(null)
    const inFlight = useRef(false)
    useEffect(() => () => controller.current?.abort(), [])
    async function submit(event: FormEvent) {
        event.preventDefault(); setError('')
        if (inFlight.current || readingFile.current) return
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
        if (!file || inFlight.current || readingFile.current) return
        readingFile.current = true; setImporting(true)
        setError(''); setResult(null)
        try {
            if (!file.name.toLowerCase().endsWith('.json')) throw new Error('Vui lòng chọn tệp JSON.')
            if (file.size > 1024 * 1024) throw new Error('File JSON tối đa 1 MB.')
            const draft = importDraft(JSON.parse(await file.text()))
            setName(draft.name); setDescription(draft.description); setQueries(draft.queries)
            setSelectedFile(file)
        } catch (reason) { setError(reason instanceof Error ? reason.message : 'Không đọc được JSON.') }
        finally { readingFile.current = false; setImporting(false) }
    }
    return <section className={`${layout.page} ${styles.page}`}>
        <ExperimentPageHeader title="Dữ liệu đánh giá" />
        <div className={styles.stack}>
            <form onSubmit={event => void submit(event)} className={`${layout.card} ${layout.form} ${styles.card}`}>
                <div className={styles.sectionHeading}><h2>Thông tin bộ dữ liệu</h2></div>
                <label className={layout.field}>Tên dataset<input value={name} maxLength={150} required placeholder="Nhập tên bộ dữ liệu đánh giá" onChange={e => setName(e.target.value)} /></label>
                <details className={styles.disclosure}><summary>Mô tả (tùy chọn)</summary><label className={layout.field}>Mô tả<textarea className={styles.textarea} value={description} maxLength={1000} onChange={e => setDescription(e.target.value)} /></label></details>
                <div className={styles.stack}>
                    <span className={styles.fieldLabel}>Tệp dữ liệu đánh giá</span>
                    <input ref={fileInput} className={upload.fileInput} type="file" accept=".json,application/json" disabled={busy || importing} aria-label="Chọn tệp JSON" onChange={e => { void loadFile(e.target.files?.[0]); e.target.value = '' }} />
                    {!selectedFile ? <button type="button" disabled={busy || importing}
                        className={`${upload.uploadArea} ${dragging ? upload.uploadAreaDragging : ''}`}
                        onClick={() => fileInput.current?.click()}
                        onDragEnter={event => { event.preventDefault(); if (!busy && !importing) setDragging(true) }}
                        onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = busy || importing ? 'none' : 'copy' }}
                        onDragLeave={event => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false) }}
                        onDrop={event => {
                            event.preventDefault(); setDragging(false)
                            if (busy || importing) return
                            if (event.dataTransfer.files.length !== 1) { setError('Vui lòng chọn một tệp JSON.'); return }
                            void loadFile(event.dataTransfer.files[0])
                        }}>
                        <span className={upload.uploadIcon}><Upload aria-hidden="true" /></span>
                        <strong>{importing ? 'Đang đọc tệp…' : dragging ? 'Thả tệp vào đây' : 'Chọn tệp dữ liệu đánh giá'}</strong>
                        <span className={upload.uploadHint}>Kéo thả tệp vào đây hoặc nhấn để chọn</span>
                        <span className={upload.fileTypes}>JSON · Tối đa 1 MB</span>
                    </button> : <div className={upload.selectedFile}>
                        <span className={upload.selectedFileIcon}><FileJson aria-hidden="true" /></span>
                        <span className={upload.selectedFileInfo}><strong title={selectedFile.name}>{selectedFile.name}</strong><span>{selectedFile.size < 1024 ? `${selectedFile.size} B` : `${(selectedFile.size / 1024).toFixed(1)} KB`}</span></span>
                        <button type="button" className={upload.removeFileButton} disabled={busy || importing} aria-label="Bỏ tệp đã chọn" onClick={() => {
                            setSelectedFile(null); setName(''); setDescription(''); setQueries([blankQuery(1)]); setResult(null); setError('')
                        }}><X size={16} aria-hidden="true" /></button>
                    </div>}
                </div>
                <div className={styles.sectionHeading}><h2>Truy vấn đánh giá</h2><div>{queries.length} truy vấn · Nhãn 0–3 · Từ 1 là liên quan</div></div>
                {queries.map((row, index) => <details className={styles.query} key={index} open={index === 0}>
                    <summary>{row.query_id || `Truy vấn ${index + 1}`} · {row.query || 'Truy vấn mới'}{row.candidates ? ` · ${row.candidates.filter(item => item.grade !== null).length}/${row.candidates.length} đã chấm` : ''}</summary>
                    <div className={styles.stack}>
                        <label className={layout.field}>Mã truy vấn<input aria-label={`Mã truy vấn ${index + 1}`} value={row.query_id} maxLength={50}
                            onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, query_id: e.target.value } : item))} /></label>
                        <label className={layout.field}>Nội dung<input aria-label={`Nội dung truy vấn ${index + 1}`} value={row.query} maxLength={300} required
                            onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, query: e.target.value } : item))} /></label>
                        {row.candidates ? <>
                            <div className={styles.muted}>Đã chấm {row.candidates.filter(item => item.grade !== null).length}/{row.candidates.length} học liệu</div>
                            {row.candidates.map(item => <div className={styles.query} key={item.id}>
                                <label className={layout.field}>{item.title ? `${item.title} (${item.id})` : item.id}
                                    <select aria-label={`Điểm ${row.query_id} ${item.id}`} value={item.grade ?? ''}
                                        onChange={e => { const grade = e.target.value === '' ? null : Number(e.target.value); setQueries(rows => rows.map((entry, i) => i === index ? setDraftGrade(entry, item.id, grade) : entry)) }}>
                                        <option value="">Chưa chấm</option><option value="0">0 — Không liên quan</option><option value="1">1 — Ít liên quan</option><option value="2">2 — Liên quan</option><option value="3">3 — Rất liên quan</option>
                                    </select>
                                </label>
                                {item.description && <details><summary>Nội dung ứng viên</summary><div>{item.description}</div></details>}
                            </div>)}
                        </> : <label className={layout.field}>Nhãn liên quan<textarea aria-label={`Nhãn truy vấn ${index + 1}`} className={styles.textarea} value={row.labels} placeholder="resource_id:grade — mỗi dòng một nhãn"
                            onChange={e => setQueries(rows => rows.map((item, i) => i === index ? { ...item, labels: e.target.value } : item))} /></label>}
                        <div className={styles.row}><button type="button" className={layout.secondaryButton} disabled={queries.length === 1} onClick={() => setQueries(rows => rows.filter((_, i) => i !== index))}>Xóa truy vấn</button></div>
                    </div>
                </details>)}
                <div className={styles.row}><button type="button" className={layout.secondaryButton} disabled={queries.length >= 20} onClick={() => setQueries(rows => [...rows, blankQuery(rows.length + 1, rows)])}>Thêm truy vấn</button><span className={styles.muted}>Tối đa 20 truy vấn.</span></div>
                {error && <div role="alert" className={styles.error}>{error}</div>}
                <div className={styles.formFooter}><button className={layout.primaryButton} disabled={busy || importing}>{busy ? 'Đang lưu…' : 'Tạo dataset'}</button></div>
            </form>
            {result && <div className={`${layout.card} ${styles.card}`} role="status">
                <h2>Bộ dữ liệu đã sẵn sàng</h2>
                <div className={styles.success}>Đã lưu {result.number_of_queries} truy vấn · {result.relevant_items} nhãn dương.</div>
                <div className={styles.identifier}>Dataset ID: {result.dataset_id}</div>
                <div className={styles.row}><button className={layout.secondaryButton} onClick={() => { void navigator.clipboard.writeText(result.dataset_id).then(() => setCopied(true)).catch(() => setError('Không sao chép được ID.')) }}>{copied ? 'Đã sao chép' : 'Sao chép ID'}</button>
                    <Link className={layout.primaryButton} to={`/experiments/run?dataset_id=${result.dataset_id}`}>Chạy thực nghiệm</Link></div>
                {visibleWarnings(result.validation.warnings).map(code => <div className={styles.muted} key={code}>{warning(code)}</div>)}
            </div>}
        </div>
    </section>
}

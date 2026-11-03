import { useEffect, useState } from 'react'
import { getOriginalOCRPage } from '../services/processingApi'
import type { OriginalOCRPage } from '../services/processingApi'
import styles from '../pages/library/LibraryPages.module.css'

const textPageSize = 10000

export function OriginalOCRPanel({ versionId, jobId, totalPages, refreshKey }: {
    versionId: string; jobId: string; totalPages: number; refreshKey: string
}) {
    const [expanded, setExpanded] = useState(false)
    const [page, setPage] = useState(1)
    const [offset, setOffset] = useState(0)
    const [data, setData] = useState<OriginalOCRPage | null>(null)
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)
    const [attempt, setAttempt] = useState(0)

    useEffect(() => {
        if (!expanded) return
        const controller = new AbortController()
        setLoading(true)
        setError('')
        setData(null)
        void getOriginalOCRPage(versionId, jobId, page, offset, controller.signal).then(value => {
            if (!controller.signal.aborted) setData(value)
        }).catch(reason => {
            if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được văn bản OCR.')
        }).finally(() => {
            if (!controller.signal.aborted) setLoading(false)
        })
        return () => controller.abort()
    }, [expanded, versionId, jobId, page, offset, attempt, refreshKey])

    return <section className={styles.contentSection}>
        <button className={styles.secondaryButton} type="button" aria-expanded={expanded} aria-controls="original-ocr-content"
            onClick={() => setExpanded(value => !value)}>
            {expanded ? 'Ẩn văn bản OCR gốc' : 'Xem văn bản OCR gốc'}
        </button>
        {expanded && <div id="original-ocr-content" aria-busy={loading}>
            <p className={styles.contentPagination}>
                <button className={styles.secondaryButton} type="button" disabled={loading || page <= 1} onClick={() => { setPage(value => value - 1); setOffset(0) }}>Trang trước</button>
                {' '}Trang {page} / {totalPages}{' '}
                <button className={styles.secondaryButton} type="button" disabled={loading || page >= totalPages} onClick={() => { setPage(value => value + 1); setOffset(0) }}>Trang tiếp</button>
            </p>
            {loading && <p role="status">Đang tải văn bản OCR...</p>}
            {error && <p className={styles.alert} role="alert">{error} <button className={styles.secondaryButton} type="button" onClick={() => setAttempt(value => value + 1)}>Thử lại</button></p>}
            {!loading && !error && data && !data.available && <p>Trang chưa có kết quả OCR. Trạng thái: {data.status}.</p>}
            {!loading && !error && data?.available && data.total_characters === 0 && <p>Trang đã xử lý và không có văn bản.</p>}
            {!loading && !error && data?.available && !!data.text &&
                <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{data.text}</pre>}
            {data?.available && (data.total_characters ?? 0) > textPageSize && <p className={styles.contentPagination}>
                <button className={styles.secondaryButton} type="button" disabled={loading || offset === 0} onClick={() => setOffset(value => Math.max(0, value - textPageSize))}>Đoạn trước</button>
                {' '}Ký tự {offset + 1}–{Math.min(offset + textPageSize, data.total_characters ?? 0)} / {data.total_characters}{' '}
                <button className={styles.secondaryButton} type="button" disabled={loading || offset + textPageSize >= (data.total_characters ?? 0)} onClick={() => setOffset(value => value + textPageSize)}>Đoạn tiếp</button>
            </p>}
        </div>}
    </section>
}

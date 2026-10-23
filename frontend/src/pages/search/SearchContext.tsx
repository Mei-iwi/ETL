import { useEffect, useState } from 'react'
import { getChunkContext } from '../../services/searchApi'
import type { ChunkContext } from '../../types/search'
import layout from '../library/LibraryPages.module.css'
import styles from './SearchPages.module.css'

export function SearchContext({ id, evidence }: { id: string; evidence?: string | null }) {
    const [open, setOpen] = useState(false)
    return <div>
        <button className={layout.primaryButton} aria-expanded={open} onClick={() => setOpen(value => !value)}>
            {open ? 'Đóng ngữ cảnh' : 'Ngữ cảnh'}
        </button>
        {open && <ContextContent key={id} id={id} evidence={evidence} />}
    </div>
}

function ContextContent({ id, evidence }: { id: string; evidence?: string | null }) {
    const [data, setData] = useState<ChunkContext | null>(null)
    const [error, setError] = useState('')
    const [attempt, setAttempt] = useState(0)
    useEffect(() => {
        const controller = new AbortController()
        void Promise.resolve().then(() => {
            if (controller.signal.aborted) return
            return getChunkContext(id, controller.signal)
        }).then(result => { if (result && !controller.signal.aborted) setData(result) })
            .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Không tải được ngữ cảnh.') })
        return () => controller.abort()
    }, [id, attempt])
    return <section className={styles.context} aria-label="Ngữ cảnh học liệu">
        {error ? <><p role="alert" className={styles.error}>{error}</p>
            <button className={layout.primaryButton} onClick={() => { setError(''); setAttempt(value => value + 1) }}>Thử lại ngữ cảnh</button></> :
            !data ? <p role="status" className={styles.pending}>Đang tải ngữ cảnh…</p> : <>
                {evidence === 'bag' && <p className={styles.metadata}>Học liệu được tìm thấy nhưng chưa xác định được đoạn khớp trực tiếp với truy vấn. Nội dung dưới đây là đoạn tham khảo.</p>}
                <p className={styles.metadata}>{data.heading_path.map(item => item.title || item.unit_type).join(' › ')}</p>
                {data.chunks.map(chunk => <div key={chunk.id} className={chunk.selected ? styles.selected : undefined}>
                    <p className={styles.metadata}>{chunk.selected ? (evidence === 'lexical' ? 'Đoạn có từ/cụm từ khớp truy vấn' : evidence === 'semantic' ? 'Đoạn gần nghĩa với truy vấn' : 'Đoạn tham khảo được chọn') : 'Đoạn liền kề'} · Trang {chunk.page_from ?? '—'}–{chunk.page_to ?? '—'}
                        {' · '}Ký tự {chunk.char_start}–{chunk.char_end}</p>
                    <p className={styles.snippet}>{chunk.text}</p>
                </div>)}
            </>}
    </section>
}

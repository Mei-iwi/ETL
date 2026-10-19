import { useCallback, useEffect, useRef, useState } from "react"
import type { ReactNode } from 'react'
import styles from './SystemCapalitiesPage.module.css'

type CapabilitiesData = {
    api_version: string
    formats: Array<{
        extension: string
        mime_type: string
        native_text_extraction: boolean
        upload_accepted: boolean
        text_extraction_supported: boolean
        ocr_requires_runtime: boolean
    }>
    master_sync: {
        source: string
        streams: string[]
        modes: string[]
        auto_sync_supported: boolean
    }
    processing: {
        ocr_engine: string
        normalized_content: boolean
        scanned_pdf_ocr: boolean
        fake_ocr_demo: boolean
        chunking: {
            enabled: boolean
            size_words: number
            overlap_words: number
        }
    }
    representations: {
        bag_of_words: boolean
        vector: {
            enabled: boolean
            method: string | null
            semantic: boolean
            dimensions: number | null
            implemented?: boolean
            runtime_status?: string
            configured?: boolean
            runtime_verified?: boolean
            runtime_scope?: string
        }
    }
    retrieval: {
        modes: string[]
        filters: string[]
        methods?: Array<{ mode: string; requires_embedding: boolean; runtime_status: string }>
        context_supported?: boolean
        pagination_supported?: boolean
        access_policy?: string
        public_search_supported?: boolean
    }
    storage: {
        metadata: string
        content_and_index: string
        files: string
        mongodb_enabled: boolean
    }
}

export function SystemCapalitiesPage() {

    const [data, setData] = useState<CapabilitiesData | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const controllerRef = useRef<AbortController | null>(null)

    const loadCapabilities = useCallback(async () => {
        controllerRef.current?.abort()

        const controller = new AbortController()
        controllerRef.current = controller

        setLoading(true)
        setError('')

        try {
            const response = await fetch(
                '/api/v1/system/capabilities',
                {
                    signal: controller.signal,
                    headers: {
                        Accept: 'application/json'
                    },
                },
            )

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`)
            }

            const result: CapabilitiesData = await response.json()

            if (controller.signal.aborted || controllerRef.current !== controller) return
            setData(result)

        } catch (e) {
            if (
                e instanceof DOMException && e.name === 'AbortError'
            ) {
                return
            }

            setError(
                'Không thể tải khả năng xử lý. Vui lòng thử lại.'
            )
        }
        finally {
            if (!controller.signal.aborted) {
                setLoading(false)
            }
        }

    }, [])

    useEffect(() => {

        const initialLoad = window.setTimeout(() => void loadCapabilities(), 0)
        return () => {
            window.clearTimeout(initialLoad)
            controllerRef.current?.abort()
        }
    }, [loadCapabilities])

    const vector = data?.representations.vector
    const vectorLabel = vector?.enabled ? 'Model hoạt động'
        : vector?.runtime_status === 'BLOCKED_EMBEDDING_RUNTIME' ? 'Model chưa khả dụng'
        : vector?.runtime_status === 'NOT_VERIFIED' ? 'Chưa xác minh model'
        : vector?.implemented ? 'Đã tích hợp' : 'Chưa tích hợp'

    return (
        <section className={styles.page} aria-labelledby="capabilities-title">
            <header className={styles.header}>
                <div><h1 id="capabilities-title">Khả năng xử lý</h1></div>
                <button className={styles.button} type="button" onClick={() => void loadCapabilities()} disabled={loading}>
                    {loading ? 'Đang tải…' : 'Tải lại'}
                </button>
            </header>
            {error && <div className={styles.notice} role="alert">{error}</div>}
            {loading && !data && <div className={styles.loading} role="status">Đang tải khả năng xử lý…</div>}
            {data && <div aria-busy={loading}>
                <div className={styles.sectionHeader}><h2>Định dạng tài liệu</h2></div>
                <article className={styles.formats}>
                    <div className={styles.tableWrapper} tabIndex={0} role="region" aria-label="Định dạng tài liệu hỗ trợ">
                        <table className={styles.table}>
                            <thead><tr><th scope="col">Định dạng</th><th scope="col">Tải lên</th><th scope="col">Đọc văn bản</th></tr></thead>
                            <tbody>{data.formats.map(format => <tr key={format.extension}>
                                <td><span className={styles.fileType}>{format.extension.replace(/^\./, '').toUpperCase()}</span></td>
                                <td><Support enabled={format.upload_accepted} /></td>
                                <td>{format.text_extraction_supported ? format.ocr_requires_runtime ? <span className={`${styles.badge} ${styles.checking}`}>OCR · Cần Tesseract</span> : <Support enabled /> : <Support enabled={false} />}</td>
                            </tr>)}</tbody>
                        </table>
                    </div>
                </article>

                <div className={styles.sectionHeader}><h2>Công cụ & xử lý</h2></div>
                <div className={styles.featureGrid}>
                    <article className={styles.feature}>
                        <h3>Đọc & chuẩn hóa văn bản</h3>
                        <dl className={styles.rows}>
                            <Info label="Đọc PDF dạng ảnh"><Support enabled={data.processing.scanned_pdf_ocr} label={data.processing.scanned_pdf_ocr ? 'Cần Tesseract' : undefined} pending={data.processing.scanned_pdf_ocr} /></Info>
                            <Info label="Chuẩn hóa nội dung"><Support enabled={data.processing.normalized_content} /></Info>
                            <Info label="Chia đoạn văn bản"><Support enabled={data.processing.chunking.enabled} /></Info>
                        </dl>
                        {data.processing.fake_ocr_demo && <div className={styles.inlineNote}>OCR đang ở chế độ giả lập thử nghiệm</div>}
                    </article>
                    <article className={styles.feature}>
                        <h3>Tìm kiếm tài liệu</h3>
                        <dl className={styles.rows}>
                            {data.retrieval.modes.map(mode => {
                                const status = data.retrieval.methods?.find(method => method.mode === mode)?.runtime_status
                                const ready = status === 'READY' || status === 'NOT_REQUIRED'
                                return <Info key={mode} label={({ bow: 'Từ khóa', vector: 'Ngữ nghĩa', hybrid: 'Kết hợp' } as Record<string, string>)[mode] ?? mode}>
                                    <Support enabled={ready} pending={!ready && status !== 'BLOCKED_EMBEDDING_RUNTIME'} label={ready ? 'Sẵn sàng' : status === 'BLOCKED_EMBEDDING_RUNTIME' ? 'Model chưa khả dụng' : status === 'NOT_VERIFIED' ? 'Chưa xác minh model' : 'Đã tích hợp'} />
                                </Info>
                            })}
                            {!data.retrieval.modes.length && <Info label="Tìm kiếm"><Support enabled={false} /></Info>}
                            <Info label="Xem ngữ cảnh"><Support enabled={!!data.retrieval.context_supported} /></Info>
                            <Info label="Phân trang"><Support enabled={!!data.retrieval.pagination_supported} /></Info>
                        </dl>
                    </article>
                    <article className={styles.feature}>
                        <h3>Đồng bộ dữ liệu</h3>
                        <dl className={styles.rows}>
                            <Info label="Nguồn dữ liệu">{data.master_sync.source === 'postgresql' ? 'PostgreSQL' : data.master_sync.source === 'json' ? 'JSON thử nghiệm' : data.master_sync.source}</Info>
                            <Info label="Đồng bộ tự động"><Support enabled={data.master_sync.auto_sync_supported} /></Info>
                            <Info label="Chế độ">{data.master_sync.modes.map(mode => ({ manual: 'Thủ công', auto: 'Tự động', full: 'Toàn bộ', incremental: 'Phần thay đổi' } as Record<string, string>)[mode] ?? mode).join(', ') || 'Chưa cấu hình'}</Info>
                        </dl>
                    </article>
                    <article className={styles.feature}>
                        <h3>Biểu diễn nội dung</h3>
                        <dl className={styles.rows}>
                            <Info label="Vector ngữ nghĩa"><Support enabled={data.representations.vector.enabled} pending={!data.representations.vector.enabled && data.representations.vector.runtime_status !== 'BLOCKED_EMBEDDING_RUNTIME'} label={vectorLabel} /></Info>
                            <Info label="Từ khóa (Bag of Words)"><Support enabled={data.representations.bag_of_words} /></Info>
                        </dl>
                    </article>
                </div>


            </div>}
        </section>
    )
}

function Info({ label, children }: { label: string; children: ReactNode }) {
    return <div className={styles.row}><dt>{label}</dt><dd>{children}</dd></div>
}

function Support({ enabled, label, pending = false }: { enabled: boolean; label?: string; pending?: boolean }) {
    return <span className={`${styles.support} ${pending ? styles.pendingText : enabled ? styles.successIcon : styles.muted}`}>
        {label ?? (enabled ? 'Hỗ trợ' : 'Chưa hỗ trợ')}
    </span>
}

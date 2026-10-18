import { useCallback, useEffect, useRef, useState } from "react"
import styles from './SystemCapbilitiesPage.module.css'

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
                e instanceof Error ? e.message : 'Lỗi tải dữ liệu'
            )
        }
        finally {
            if (!controller.signal.aborted) {
                setLoading(false)
            }
        }

    }, [])

    useEffect(() => {

        void loadCapabilities()
        return () => controllerRef.current?.abort()
    }, [loadCapabilities])

    return (
        <section className={styles.page}>
            <header className={styles.pageHeader}>
                <div>
                    <h1>Khả năng xử lý</h1>
                    {data && <p>Phiên bản API: {data.api_version}</p>}
                </div>
                <button className={styles.reloadButton} type="button" onClick={() => void loadCapabilities()} disabled={loading}>{loading ? 'Đang tải...' : 'Tải lại'}</button>
            </header>
            {error && (
                <p className={styles.error}>Lỗi tải dữ liệu: {error}</p>
            )}
            {loading && !data && (
                <p>Đang tải dữ liệu ...</p>
            )}
            {data && <div className={styles.grid}>
                <article className={styles.card}>
                    <h2>Đồng bộ dữ liệu nguồn</h2>
                    <p>Nguồn: {data.master_sync.source === 'postgresql' ? 'PostgreSQL' : 'JSON thử nghiệm'}</p>
                    <p>Chế độ: {data.master_sync.modes.join(', ')}</p>
                    <p>Tự động đồng bộ: {data.master_sync.auto_sync_supported ? 'Có hỗ trợ' : 'Không hỗ trợ trong cấu hình hiện tại'}</p>
                </article>
                <article className={`${styles.card} ${styles.formatsCard}`}>
                    <h2>Định dạng hỗ trợ</h2>
                    <div className={styles.tableWrapper} tabIndex={0} role="region" aria-label="Định dạng hỗ trợ">
                        <table className={styles.formatsTable}>
                            <thead>
                                <tr>
                                    <th scope="col">Định dạng</th>
                                    <th scope="col">MIME type</th>
                                    <th scope="col">Tiếp nhận</th>
                                    <th scope="col">Trích xuất văn bản</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.formats.map((format) => (
                                    <tr key={format.extension}>
                                        <td><strong>{format.extension}</strong></td>
                                        <td>{format.mime_type}</td>
                                        <td>{format.upload_accepted ? 'Có' : 'Không'}</td>
                                        <td>{format.text_extraction_supported ? format.ocr_requires_runtime ? 'OCR, cần Tesseract' : 'Có' : 'Chưa hỗ trợ trong cấu hình hiện tại'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </article>
                <article className={styles.card}>
                    <h2>Trích xuất và chuẩn hóa</h2>
                    <p>Engine: {data.processing.ocr_engine}</p>
                    <p>OCR PDF scan: {data.processing.scanned_pdf_ocr ? 'Có hỗ trợ, cần Tesseract và dữ liệu ngôn ngữ' : 'Chưa hỗ trợ trong cấu hình hiện tại'}</p>
                    <p>Chuẩn hóa nội dung: {data.processing.normalized_content ? 'Đã hỗ trợ' : 'Chưa hỗ trợ'}</p>
                    {data.processing.fake_ocr_demo && <p>OCR giả lập, chỉ dùng thử nghiệm.</p>}
                </article>
                <article className={styles.card}>
                    <h2>Phân đoạn văn bản</h2>
                    <p>
                        Trạng thái: {' '}
                        {data.processing.chunking.enabled ? 'Đã tích hợp' : 'Chưa tích hợp vào pipeline'}
                    </p>
                    <p>
                        Kích thước cấu hình: {' '}
                        {data.processing.chunking.size_words} từ
                    </p>
                    <p>
                        Chồng lấn: {' '}
                        {data.processing.chunking.overlap_words}
                    </p>
                </article>
                <article className={styles.card}>
                    <h2>Biểu diễn vector</h2>
                    <p>
                        Trạng thái: {' '}
                        {data.representations.vector.enabled ? 'Model đã chạy thành công' : data.representations.vector.runtime_status === 'BLOCKED_EMBEDDING_RUNTIME' ? 'Model chưa cấu hình hoặc không khả dụng' : data.representations.vector.runtime_status === 'NOT_VERIFIED' ? 'Model local đã cấu hình, chưa xác minh hoạt động' : data.representations.vector.implemented ? 'Đã tích hợp' : 'Chưa tích hợp'}
                    </p>
                    <p>
                        Phương pháp: {' '}
                        {data.representations.vector.method ?? 'Chưa cấu hình'}
                    </p>
                    <p>
                        Số chiều cấu hình: {' '}
                        {data.representations.vector.dimensions ?? 'Chưa xác nhận'}
                    </p>
                    <p>Bag of Words: {data.representations.bag_of_words ? 'Đã tích hợp' : 'Chưa tích hợp vào pipeline'}</p>
                </article>
                <article className={styles.card}>
                    <h2>Tra cứu nội dung</h2>
                    {data.retrieval.modes.length ? <ul className={styles.searchMethods}>
                        {data.retrieval.modes.map(mode => {
                            const status = data.retrieval.methods?.find(method => method.mode === mode)?.runtime_status
                            const label = ({ bow: 'BoW · Từ khóa', vector: 'Vector · Ngữ nghĩa', hybrid: 'Hybrid · Kết hợp' } as Record<string, string>)[mode] ?? mode
                            return <li key={mode}>
                                <span>{label}</span>
                                <span className={status === 'READY' || status === 'NOT_REQUIRED' ? styles.ready : status === 'BLOCKED_EMBEDDING_RUNTIME' ? styles.blocked : styles.pending}>
                                    {status === 'NOT_REQUIRED' ? 'Không cần model' : status === 'READY' ? 'Model hoạt động' : status === 'BLOCKED_EMBEDDING_RUNTIME' ? 'Model chưa khả dụng' : status === 'NOT_VERIFIED' ? 'Đã cấu hình model' : 'Đã tích hợp'}
                                </span>
                            </li>
                        })}
                    </ul> : <p>Chưa tích hợp tìm kiếm</p>}
                    <p>Bộ lọc: {data.retrieval.filters.map(filter => ({
                        subject_id: 'Môn học', grade_level_id: 'Khối lớp', resource_type_code: 'Loại học liệu',
                        resource_id: 'Học liệu', resource_version_id: 'Phiên bản',
                    } as Record<string, string>)[filter] ?? filter).join(', ') || 'Chưa hỗ trợ'}</p>
                    {data.retrieval.context_supported && <p>Xem ngữ cảnh: Có hỗ trợ</p>}
                    {data.retrieval.pagination_supported && <p>Phân trang kết quả: Có hỗ trợ</p>}
                </article>
                <article className={styles.card}>
                    <h2>Lưu trữ</h2>
                    <p>Metadata: {data.storage.metadata}</p>
                    <p>
                        Nội dung và chỉ mục: {' '}
                        {data.storage.content_and_index}
                    </p>
                    <p>
                        Tệp: {data.storage.files}
                    </p>
                    <p>
                        MongoDB: {' '}
                        {data.storage.mongodb_enabled ? 'Đã cấu hình' : 'Không sử dụng'}
                    </p>
                </article>
            </div>}

        </section>

    )
}

import { useCallback, useEffect, useRef, useState } from "react"
import styles from './SystemCapbilitiesPage.module.css'

type CapabilitiesData = {
    api_version: string
    formats: Array<{
        extension: string
        mime_type: string
        native_text_extractioin: boolean
    }>
    master_sync: {
        source: string
        streams: string[]
        modes: string[]
    }
    processing: {
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
            method: string
            semantic: boolean
            dimensions: number
        }
    }
    retrieval: {
        modes: string[]
        filters: string[]
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
        <section>
            <h1>Khả năng xử lý</h1>
            <button className={styles.reloadButton} type="button" onClick={() => void loadCapabilities()} disabled={loading}>{loading ? 'Đang tải...' : 'Tải lại'}</button>
            {error && (
                <p className={styles.error}>Lỗi tải dữ liệu: {error}</p>
            )}
            {loading && !data && (
                <p>Đang tải dữ liệu ...</p>
            )}
            {data && <div className={styles.grid}>
                <article className={styles.card}>
                    <h2>API</h2>
                    <p>Phiên bản: {data.api_version}</p>
                </article>
                <article className={styles.card}>
                    <h2>Định dạng hỗ trợ</h2>
                    {data.formats.map((format) => (
                        <div key={format.extension}>
                            <strong>{format.extension}</strong>
                            <p>{format.mime_type}</p>
                            <p>
                                Trích xuất văn bản: {' '} {format.native_text_extractioin ? 'Có' : 'Không'}
                            </p>
                        </div>
                    ))}
                </article>
                <article className={styles.card}>
                    <h2>Phân đoạn văn bản</h2>
                    <p>
                        Trạng thái: {' '}
                        {data.processing.chunking.enabled ? 'Đang bật' : 'Đang tắt'}
                    </p>
                    <p>
                        Kích thước: {' '}
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
                        {data.representations.vector.enabled ? 'Đang bật' : 'Đang tắt'}
                    </p>
                    <p>
                        Phương pháp: {' '}
                        {data.representations.vector.method}
                    </p>
                    <p>
                        Số chiều: {' '}
                        {data.representations.vector.dimensions}
                    </p>
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
                        {data.storage.mongodb_enabled ? 'Đang bật' : 'Đăng tắt'}
                    </p>
                </article>
            </div>}

        </section>

    )
}

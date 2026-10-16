import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
    getResourceTypes,
    type ResourceTypeOption,
} from '../../services/libraryLookupApi'
import styles from './LibraryListPage.module.css'

type Resource = {
    id: string
    title: string
    provider_name: string
    resource_type_name_vi: string
    publication_status: string
    updated_at: string
    latest_version?: {
        id: string
        version_number: number
        lifecycle_status: string
        processing_status: string
        created_at: string
        source_version_id: string | null
    } | null
}

type ResourceListResponse = {
    items: Resource[]
    pagination: {
        page: number
        size: number
        total: number
        total_pages: number
    }
}

export function LibraryListPage() {
    const [keyword, setKeyword] = useState('')
    const [query, setQuery] = useState('')
    const [page, setPage] = useState(1)
    const [resourceTypeCode, setResourceTypeCode] = useState('')
    const [resourceTypes, setResourceTypes] = useState<ResourceTypeOption[]>([])
    const [lookupLoading, setLookupLoading] = useState(true)
    const [lookupError, setLookupError] = useState('')
    const [data, setData] = useState<ResourceListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [refresh, setRefresh] = useState(0)

    useEffect(() => {
        const controller = new AbortController()

        async function loadResourceTypes() {
            try {
                const items = await getResourceTypes(controller.signal)
                if (controller.signal.aborted) return
                setResourceTypes(items)
                if (items.length === 0) {
                    setLookupError('Danh mục loại học liệu đang trống.')
                }
            } catch (err) {
                if (controller.signal.aborted) return
                setLookupError(
                    err instanceof Error
                        ? err.message
                        : 'Không thể tải danh mục loại học liệu.',
                )
            } finally {
                if (!controller.signal.aborted) setLookupLoading(false)
            }
        }

        void loadResourceTypes()
        return () => controller.abort()
    }, [])

    useEffect(() => {
        const controller = new AbortController()

        async function loadResources() {
            setLoading(true)
            setError('')

            const params = new URLSearchParams({
                page: String(page),
                size: '7',
                q: query,
            })
            if (resourceTypeCode) {
                params.set('resource_type_code', resourceTypeCode)
            }

            try {
                const response = await fetch(
                    `/api/v1/resources?${params.toString()}`,
                    { signal: controller.signal },
                )

                if (!response.ok) {
                    throw new Error(`Không tải được danh sách (HTTP ${response.status})`)
                }

                const result: ResourceListResponse = await response.json()
                if (controller.signal.aborted) return
                setData(result)
            } catch (err) {
                if (controller.signal.aborted) return

                setError(
                    err instanceof Error ? err.message : 'Đã xảy ra lỗi khi tải học liệu.',
                )
            } finally {
                if (!controller.signal.aborted) {
                    setLoading(false)
                }
            }
        }

        void loadResources()

        return () => controller.abort()
    }, [page, query, resourceTypeCode, refresh])

    function handleSearch(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        setPage(1)
        setQuery(keyword.trim())
    }

    return (
        <section className={styles.page}>
            <header className={styles.header}>
                <div>
                    <h1>Danh sách học liệu</h1>
                </div>
            </header>

            <form className={styles.searchForm} onSubmit={handleSearch}>
                <input
                    aria-label="Tìm học liệu"
                    value={keyword}
                    onChange={(event) => setKeyword(event.target.value)}
                    placeholder="Nhập tên học liệu..."
                />
                <select
                    aria-label="Lọc theo loại học liệu"
                    value={resourceTypeCode}
                    onChange={(event) => {
                        setPage(1)
                        setResourceTypeCode(event.target.value)
                    }}
                    disabled={lookupLoading || Boolean(lookupError)}
                >
                    <option value="">Tất cả loại học liệu</option>
                    {resourceTypes.map((resourceType) => (
                        <option key={resourceType.code} value={resourceType.code}>
                            {resourceType.name}
                        </option>
                    ))}
                </select>
                <button type="submit">Tìm kiếm</button>
                <button type="button" disabled={loading} onClick={() => setRefresh(value => value + 1)}>Tải lại</button>
            </form>

            {lookupError && <p className={styles.error} role="alert">{lookupError}</p>}
            {error && <p className={styles.error} role="alert">{error}</p>}

            <div className={styles.tableWrapper}>
                <table className={styles.table}>
                    <thead>
                        <tr>
                            <th>Tên học liệu</th>
                            <th>Đơn vị cung cấp</th>
                            <th>Loại học liệu</th>
                            <th>Phát hành</th>
                            <th>Phiên bản / xử lý</th>
                            <th>Cập nhật</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr>
                                <td colSpan={7} className={styles.message}>
                                    Đang tải danh sách học liệu...
                                </td>
                            </tr>
                        ) : data?.items.length ? (
                            data.items.map((resource) => (
                                <tr key={resource.id}>
                                    <td className={styles.title}>{resource.title}</td>
                                    <td>{resource.provider_name}</td>
                                    <td>{resource.resource_type_name_vi}</td>
                                    <td>{resource.publication_status}</td>
                                    <td>
                                        {resource.latest_version ? <div className={styles.versionSummary}>
                                            <Link className={styles.link} to={`/library/${encodeURIComponent(resource.id)}/versions`}
                                                title={resource.latest_version.source_version_id ? `Nguồn: ${resource.latest_version.source_version_id}` : undefined}>
                                                Phiên bản {resource.latest_version.version_number}
                                            </Link>
                                            <span className={resource.latest_version.lifecycle_status === 'REJECTED' || resource.latest_version.processing_status.includes('FAILED') ? styles.failed
                                                : resource.latest_version.processing_status === 'CONTENT_READY' ? styles.ready : styles.pending}>
                                                {resource.latest_version.lifecycle_status === 'REJECTED' ? 'Bị từ chối' : ({
                                                    NOT_STARTED: 'Chưa xử lý', OCR_PENDING: 'Đang trích xuất', OCR_FAILED: 'Lỗi trích xuất',
                                                    FINALIZING: 'Đang hoàn thiện', FINALIZATION_PENDING: 'Chờ hoàn thiện',
                                                    FINALIZATION_FAILED: 'Lỗi hoàn thiện', CONTENT_READY: 'Đã xử lý',
                                                }[resource.latest_version.processing_status] || 'Chưa rõ trạng thái')}
                                            </span>
                                            <span className={styles.versionTime}>{resource.latest_version.source_version_id ? 'Tiếp nhận' : 'Tạo'}: {new Date(resource.latest_version.created_at).toLocaleString('vi-VN')}</span>
                                        </div> : <span className={styles.versionTime}>Chưa có phiên bản</span>}
                                    </td>
                                    <td>
                                        {new Date(resource.updated_at).toLocaleDateString('vi-VN')}
                                    </td>
                                    <td>
                                        <Link
                                            className={styles.link}
                                            to={`/library/${encodeURIComponent(resource.id)}`}
                                        >
                                            Chi tiết
                                        </Link>
                                        {' · '}
                                        <Link
                                            className={styles.link}
                                            to={`/library/${encodeURIComponent(resource.id)}/versions`}
                                        >
                                            Lịch sử phiên bản
                                        </Link>
                                        {' . '}
                                        <Link
                                            className={styles.link}
                                            to={`/library/${encodeURIComponent(resource.id)}/update-version`}
                                            >
                                                Cập nhật
                                            </Link>
                                    </td>
                                </tr>
                            ))
                        ) : (
                            <tr>
                                <td colSpan={7} className={styles.message}>
                                    Không tìm thấy học liệu.
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            {data && (
                <footer className={styles.pagination}>
                    <span>
                        Tổng {data.pagination.total} học liệu
                    </span>
                    <div>
                        <button
                            type="button"
                            onClick={() => setPage((current) => current - 1)}
                            disabled={page <= 1 || loading}
                        >
                            Trước
                        </button>
                        <span>
                            Trang {data.pagination.page} / {Math.max(data.pagination.total_pages, 1)}
                        </span>
                        <button
                            type="button"
                            onClick={() => setPage((current) => current + 1)}
                            disabled={page >= data.pagination.total_pages || loading}
                        >
                            Sau
                        </button>
                    </div>
                </footer>
            )}
        </section>
    )
}

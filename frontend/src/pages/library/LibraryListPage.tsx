import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import styles from './LibraryListPage.module.css'

type Resource = {
    id: string
    title: string
    provider_name: string
    resource_type_name_vi: string
    publication_status: string
    updated_at: string
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
    const [data, setData] = useState<ResourceListResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

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

            try {
                const response = await fetch(
                    `/api/v1/resources?${params.toString()}`,
                    { signal: controller.signal },
                )

                if (!response.ok) {
                    throw new Error(`Không tải được danh sách (HTTP ${response.status})`)
                }

                const result: ResourceListResponse = await response.json()
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
    }, [page, query])

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
                    <p>Tra cứu học liệu và xem lịch sử các phiên bản.</p>
                </div>
            </header>

            <form className={styles.searchForm} onSubmit={handleSearch}>
                <input
                    aria-label="Tìm học liệu"
                    value={keyword}
                    onChange={(event) => setKeyword(event.target.value)}
                    placeholder="Nhập tên học liệu..."
                />
                <button type="submit">Tìm kiếm</button>
            </form>

            {error && <p className={styles.error} role="alert">{error}</p>}

            <div className={styles.tableWrapper}>
                <table className={styles.table}>
                    <thead>
                        <tr>
                            <th>Tên học liệu</th>
                            <th>Đơn vị cung cấp</th>
                            <th>Loại học liệu</th>
                            <th>Trạng thái</th>
                            <th>Cập nhật</th>
                            <th></th>
                        </tr>
                    </thead>
                    <tbody>
                        {loading ? (
                            <tr>
                                <td colSpan={6} className={styles.message}>
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
                                        {new Date(resource.updated_at).toLocaleDateString('vi-VN')}
                                    </td>
                                    <td>
                                        <Link
                                            className={styles.link}
                                            to={`/library/${encodeURIComponent(resource.id)}`}
                                        >
                                            Chi tiết
                                        </Link>
                                    </td>
                                    <td>
                                        <Link
                                            className={styles.link}
                                            to={`/library/${encodeURIComponent(resource.id)}/versions`}
                                        >
                                            Lịch sử phiên bản
                                        </Link>
                                    </td>
                                </tr>
                            ))
                        ) : (
                            <tr>
                                <td colSpan={6} className={styles.message}>
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
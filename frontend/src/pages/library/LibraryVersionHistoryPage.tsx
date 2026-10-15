import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import styles from './LibraryVersionHistoryPage.module.css'

type ResourceDetail = {
    id: string
    title: string
    provider_name: string
    resource_type_name_vi: string
    updated_at: string
}

type ResourceVersion = {
    id: string
    resource_id: string
    parent_version_id: string | null
    version_number: number
    lifecycle_status: string
    uploaded_by: string | null
    created_at: string
    updated_at: string
}

export function LibraryVersionHistoryPage() {
    const { resourceId } = useParams<{ resourceId: string }>()

    const [resource, setResource] = useState<ResourceDetail | null>(null)
    const [versions, setVersions] = useState<ResourceVersion[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    useEffect(() => {
        if (!resourceId) {
            setError('Thiếu mã học liệu.')
            setLoading(false)
            return
        }

        // Đặt sau bước kiểm tra để TypeScript biết resourceId là string.
        const id = encodeURIComponent(resourceId)
        const controller = new AbortController()

        async function loadHistory() {
            setLoading(true)
            setError('')

            try {
                const [resourceResponse, versionsResponse] = await Promise.all([
                    fetch(`/api/v1/resources/${id}`, {
                        signal: controller.signal,
                    }),
                    fetch(`/api/v1/resources/${id}/versions`, {
                        signal: controller.signal,
                    }),
                ])

                if (!resourceResponse.ok || !versionsResponse.ok) {
                    throw new Error(
                        'Không tải được thông tin học liệu hoặc lịch sử phiên bản.',
                    )
                }

                const resourceData: ResourceDetail =
                    await resourceResponse.json()
                const versionsData: ResourceVersion[] =
                    await versionsResponse.json()

                setResource(resourceData)
                setVersions(versionsData)
            } catch (err) {
                if (controller.signal.aborted) return

                setError(
                    err instanceof Error
                        ? err.message
                        : 'Đã xảy ra lỗi khi tải lịch sử phiên bản.',
                )
            } finally {
                if (!controller.signal.aborted) {
                    setLoading(false)
                }
            }
        }

        void loadHistory()

        return () => controller.abort()
    }, [resourceId])

    return (
        <section className={styles.page}>
            <Link className={styles.backLink} to="/library/list">
                ← Quay lại danh sách học liệu
            </Link>

            <header className={styles.header}>
                <h1>Lịch sử phiên bản</h1>

                {resource && (
                    <div className={styles.resourceInfo}>
                        <h2>{resource.title}</h2>
                        <p>
                            {resource.provider_name} ·{' '}
                            {resource.resource_type_name_vi}
                        </p>
                        
                    </div>
                )}
            </header>

            {error && (
                <p className={styles.error} role="alert">
                    {error}
                </p>
            )}

            <div className={styles.panel}>
                <h2>Các phiên bản</h2>

                {loading ? (
                    <p className={styles.message}>
                        Đang tải lịch sử phiên bản...
                    </p>
                ) : versions.length === 0 ? (
                    <p className={styles.message}>
                        Học liệu chưa có phiên bản nào.
                    </p>
                ) : (
                    <ol className={styles.timeline}>
                        {versions.map((version) => (
                            <li
                                className={styles.version}
                                key={version.id}
                            >
                                <div className={styles.versionHeading}>
                                    <strong>
                                        Phiên bản {version.version_number}
                                    </strong>
                                    <span className={styles.status}>
                                        {version.lifecycle_status}
                                    </span>
                                </div>

                                <dl className={styles.details}>
                                    <div>
                                        <dt>Mã phiên bản</dt>
                                        <dd>{version.id}</dd>
                                    </div>

                                    <div>
                                        <dt>Người tải lên</dt>
                                        <dd>
                                            {version.uploaded_by ||
                                                'Chưa có thông tin'}
                                        </dd>
                                    </div>

                                    <div>
                                        <dt>Ngày tạo</dt>
                                        <dd>
                                            {new Date(
                                                version.created_at,
                                            ).toLocaleString('vi-VN')}
                                        </dd>
                                    </div>

                                    <div>
                                        <dt>Cập nhật gần nhất</dt>
                                        <dd>
                                            {new Date(version.updated_at).toLocaleString('vi-VN')}
                                        </dd>
                                    </div>
                                </dl>
                                <Link to={`/library/indexing?${new URLSearchParams({ resourceId: resourceId ?? '', versionId: version.id })}`}>Phân đoạn / chỉ mục →</Link>
                            </li>
                        ))}
                    </ol>
                )}
            </div>
        </section>
    )
}

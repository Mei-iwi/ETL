import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, BookOpen, History, Eye, Layers } from 'lucide-react'
import { canVisit, useAuth } from '../../auth/AuthProvider'
import styles from './LibraryVersionHistoryPage.module.css'
import detailStyles from './LibraryResourceDetailPage.module.css'

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
    const { principal } = useAuth()
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

                if (controller.signal.aborted) return
                setResource(resourceData)
                setVersions([...versionsData].sort((a, b) => b.version_number - a.version_number))
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
        <section className={detailStyles.page}>
            <Link className={detailStyles.backLink} to="/library/list">
                <ArrowLeft size={17} aria-hidden="true" />Quay lại danh sách học liệu
            </Link>

            <div className={detailStyles.profileStage}>
                {resource && <aside className={detailStyles.resourceCard} aria-label="Tóm tắt phiên bản">
                    <div className={detailStyles.resourceCardBody}>
                        <div className={detailStyles.bookPortrait}><BookOpen size={68} strokeWidth={1.2} aria-hidden="true" /></div>
                        <h2>{resource.resource_type_name_vi}</h2>
                        <span className={detailStyles.cardAccent} />
                        <p className={styles.versionCount}>{versions.length}<span>phiên bản</span></p>
                        {versions[0] && <p className={styles.latestSummary}>Phiên bản mới nhất trong danh sách<strong>Phiên bản {versions[0].version_number}</strong></p>}
                    </div>
                    <div className={detailStyles.resourceCardFooter}><History size={16} aria-hidden="true" />Lịch sử học liệu</div>
                </aside>}
                <div className={detailStyles.resourceInformation}>
                    <header className={detailStyles.header}>
                        <h1>{resource?.title || 'Lịch sử phiên bản'}</h1>
                    </header>

            {error && (
                <p className={styles.error} role="alert">
                    {error}
                </p>
            )}

            <section className={styles.panel} aria-labelledby="version-history-title">
                <div className={styles.sectionHeading}><h2 id="version-history-title">Lịch sử phiên bản</h2>
                    {!loading && !error && <span>{versions.length} phiên bản</span>}</div>

                {loading ? (
                    <p className={styles.message}>
                        Đang tải lịch sử phiên bản...
                    </p>
                ) : error ? null : versions.length === 0 ? (
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
                                    <strong><Layers size={16} aria-hidden="true" />Phiên bản {version.version_number}</strong>
                                    <span className={styles.status}>
                                        {({ INGESTED: 'Đã tiếp nhận', APPROVED: 'Đã duyệt', REJECTED: 'Đã từ chối',
                                            PROCESSING: 'Đang xử lý', ARCHIVED: 'Đã lưu trữ' } as Record<string, string>)[version.lifecycle_status] || version.lifecycle_status}
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
                                <div className={styles.actions}>
                                    <Link className={styles.viewButton} to={`/library/${encodeURIComponent(resourceId ?? '')}?${new URLSearchParams({ versionId: version.id })}`}>
                                        <Eye size={14} aria-hidden="true" />Xem phiên bản</Link>
                                    {canVisit('/library/indexing', principal) && <Link className={styles.actionButton} to={`/library/indexing?${new URLSearchParams({ resourceId: resourceId ?? '', versionId: version.id })}`}>Phân đoạn / chỉ mục</Link>}
                                </div>
                            </li>
                        ))}
                    </ol>
                )}
            </section>
                </div>
            </div>
        </section>
    )
}

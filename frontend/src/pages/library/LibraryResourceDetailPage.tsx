import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import styles from './LibraryResourceDetailPage.module.css'
import { ResourceFilePanel } from './ResourceFilePanel'

type Subject = {
    id: string
    name: string
    is_primary: boolean
}

type GradeLevel = {
    id: string
    grade_code: string
    grade_name_vi: string
}

type ResourceDetail = {
    id: string
    source_origin?: string | null
    title: string
    provider_id: string
    provider_name: string
    provider_type: string
    resource_type_code: string
    resource_type_name_vi: string
    publication_status: string
    is_active: boolean
    created_at: string
    updated_at: string
    subjects: Subject[]
    grade_levels: GradeLevel[]
}

export function LibraryResourceDetailPage() {
    const { resourceId } = useParams<{ resourceId: string }>()
    const [params] = useSearchParams()
    const returnTo = params.get('returnTo')
    const backTo = returnTo?.startsWith('/search/query-methods?') ? returnTo : '/library/list'
    const backLabel = backTo === '/library/list' ? '← Quay lại danh sách học liệu' : '← Quay lại kết quả tìm kiếm'

    const [resource, setResource] = useState<ResourceDetail | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')

    useEffect(() => {
        if (!resourceId) {
            setError('Thiếu mã học liệu.')
            setLoading(false)
            return
        }

        const id = encodeURIComponent(resourceId)
        const controller = new AbortController()

        async function loadResource() {
            setLoading(true)
            setError('')

            try {
                const response = await fetch(`/api/v1/resources/${id}`, {
                    signal: controller.signal,
                })

                if (response.status === 404) {
                    throw new Error('Không tìm thấy học liệu.')
                }

                if (!response.ok) {
                    throw new Error(`Không tải được học liệu (HTTP ${response.status}).`)
                }

                const result: ResourceDetail = await response.json()
                setResource(result)
            } catch (err) {
                if (controller.signal.aborted) return

                setError(
                    err instanceof Error
                        ? err.message
                        : 'Đã xảy ra lỗi khi tải thông tin học liệu.',
                )
            } finally {
                if (!controller.signal.aborted) {
                    setLoading(false)
                }
            }
        }

        void loadResource()

        return () => controller.abort()
    }, [resourceId])

    if (loading) {
        return (
            <section className={styles.page}>
                <p className={styles.message}>Đang tải thông tin học liệu...</p>
            </section>
        )
    }

    if (error || !resource) {
        return (
            <section className={styles.page}>
                <Link className={styles.backLink} to={backTo}>
                    {backLabel}
                </Link>
                <p className={styles.error} role="alert">
                    {error || 'Không có dữ liệu học liệu.'}
                </p>
            </section>
        )
    }

    return (
        <section className={styles.page}>
            <Link className={styles.backLink} to={backTo}>
                {backLabel}
            </Link>

            <header className={styles.header}>
                <h1 className={styles.eyebrow}>Chi tiết học liệu</h1>
                <h1>{resource.title}</h1>
            </header>

            <ResourceFilePanel key={`${resource.id}:${params.get('versionId')}`} resourceId={resource.id}
                versionId={params.get('versionId')} contextId={params.get('contextId')}
                sourceOwned={resource.source_origin === 'POSTGRESQL'} />

            <section className={styles.panel}>
                <h2>Thông tin chung</h2>

                <dl className={styles.details}>
                    <div>
                        <dt>Nguồn quản lý</dt>
                        <dd>{resource.source_origin === 'POSTGRESQL' ? 'SOURCE (chỉ đọc)' : resource.source_origin === 'LOCAL' ? 'LOCAL' : 'Chưa xác minh'}</dd>
                    </div>
                    <div>
                        <dt>Đơn vị cung cấp</dt>
                        <dd>{resource.provider_name}</dd>
                    </div>
                    <div>
                        <dt>Loại học liệu</dt>
                        <dd>{resource.resource_type_name_vi}</dd>
                    </div>
                    <div>
                        <dt>Trạng thái xuất bản</dt>
                        <dd>{resource.publication_status}</dd>
                    </div>
                    <div>
                        <dt>Trạng thái hoạt động</dt>
                        <dd>{resource.is_active ? 'Đang hoạt động' : 'Không hoạt động'}</dd>
                    </div>
                    <div>
                        <dt>Ngày tạo</dt>
                        <dd>{new Date(resource.created_at).toLocaleString('vi-VN')}</dd>
                    </div>
                    <div>
                        <dt>Cập nhật gần nhất</dt>
                        <dd>{new Date(resource.updated_at).toLocaleString('vi-VN')}</dd>
                    </div>
                </dl>
            </section>

            <section className={styles.panel}>
                <h2>Môn học</h2>

                {resource.subjects.length > 0 ? (
                    <ul className={styles.tagList}>
                        {resource.subjects.map((subject) => (
                            <li className={styles.tag} key={subject.id}>
                                {subject.name}
                                {subject.is_primary && (
                                    <span className={styles.primaryTag}>Môn chính</span>
                                )}
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p className={styles.empty}>Chưa có thông tin môn học.</p>
                )}
            </section>

            <section className={styles.panel}>
                <h2>Khối lớp</h2>

                {resource.grade_levels.length > 0 ? (
                    <ul className={styles.tagList}>
                        {resource.grade_levels.map((grade) => (
                            <li className={styles.tag} key={grade.id}>
                                {grade.grade_name_vi} ({grade.grade_code})
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p className={styles.empty}>Chưa có thông tin khối lớp.</p>
                )}
            </section>

            <Link
                className={styles.historyLink}
                to={`/library/${encodeURIComponent(resource.id)}/versions`}
            >
                Xem lịch sử phiên bản
            </Link>
        </section>
    )
}

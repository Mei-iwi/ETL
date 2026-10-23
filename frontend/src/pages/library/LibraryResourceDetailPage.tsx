import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { ArrowLeft, BookOpen, GraduationCap, Library, History } from 'lucide-react'
import styles from './LibraryResourceDetailPage.module.css'
import { ResourceFilePanel } from './ResourceFilePanel'
import { ResourceDescriptionPanel } from './ResourceDescriptionPanel'

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
    const backLabel = backTo === '/library/list' ? 'Quay lại danh sách học liệu' : 'Quay lại kết quả tìm kiếm'

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
                <ArrowLeft size={17} />{backLabel}
            </Link>

            <div className={styles.profileStage}>
                <aside className={styles.resourceCard} aria-label="Tóm tắt học liệu">
                    <div className={styles.resourceCardBody}>
                        <div className={styles.bookPortrait}><BookOpen size={68} strokeWidth={1.2} aria-hidden="true" /></div>
                        <h2>{resource.resource_type_name_vi}</h2>
                        <span className={styles.cardAccent} />
                        <div className={styles.cardTaxonomy}>
                            <h3><Library size={15} aria-hidden="true" />Môn học</h3>
                            {resource.subjects.length ? <ul className={styles.cardTags}>
                                {resource.subjects.map(subject => <li key={subject.id}>
                                    {subject.name}{subject.is_primary && <span className={styles.primaryTag}>Môn chính</span>}
                                </li>)}
                            </ul> : <p>Chưa có thông tin môn học.</p>}
                        </div>
                        <div className={styles.cardTaxonomy}>
                            <h3><GraduationCap size={16} aria-hidden="true" />Khối lớp</h3>
                            {resource.grade_levels.length ? <ul className={styles.cardTags}>
                                {resource.grade_levels.map(grade => <li key={grade.id}>{grade.grade_name_vi} ({grade.grade_code})</li>)}
                            </ul> : <p>Chưa có thông tin khối lớp.</p>}
                        </div>
                    </div>
                    <div className={styles.resourceCardFooter}><BookOpen size={16} aria-hidden="true" />Kho học liệu số</div>
                </aside>

                <div className={styles.resourceInformation}>
            <header className={styles.header}>
                <h1>{resource.title}</h1>
            </header>

            <ResourceDescriptionPanel key={resource.id} resourceId={resource.id}>
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
                        <dd>{({ PUBLISHED: 'Đã xuất bản', DRAFT: 'Bản nháp', ARCHIVED: 'Đã lưu trữ' } as Record<string, string>)[resource.publication_status] || resource.publication_status}</dd>
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
            </ResourceDescriptionPanel>
                </div>
            </div>

            <div className={styles.fileSection}>
            <ResourceFilePanel key={`${resource.id}:${params.get('versionId')}`} resourceId={resource.id}
                versionId={params.get('versionId')} contextId={params.get('contextId')}
                contextEvidence={params.get('contextEvidence')}
                sourceOwned={resource.source_origin === 'POSTGRESQL'} />

            <Link
                className={styles.historyLink}
                to={`/library/${encodeURIComponent(resource.id)}/versions`}
            >
                <History size={16} aria-hidden="true" />Xem lịch sử phiên bản
            </Link>
            </div>
        </section>
    )
}

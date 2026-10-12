import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { useUpdateResourceMetadata } from '../../hooks/useUpdateResourceMetadata'
import {
    getGradeLevels,
    getProviders,
    getResourceTypes,
    getSubjects,
    type GradeLevelOption,
    type ProviderOption,
    type ResourceTypeOption,
    type SubjectOption,
} from '../../services/libraryLookupApi'
import type {
    PublicationStatus,
    UpdateResourceMetadataRequest,
} from '../../types/resourceMetadata'
import styles from './LibraryPages.module.css'

type ResourceDetailResponse = {
    title: string
    resource_type_code: string
    provider_id: string
    publication_status: PublicationStatus
    subjects: { id: string }[]
    grade_levels: { id: string }[]
}

export function LibraryUpdateVersion() {
    const { resourceId } = useParams<{ resourceId: string }>()
    const navigate = useNavigate()
    const [title, setTitle] = useState('')
    const [resourceTypeCode, setResourceTypeCode] = useState('')
    const [providerId, setProviderId] = useState('')
    const [gradeLevelId, setGradeLevelId] = useState('')
    const [subjectId, setSubjectId] = useState('')
    const [publicationStatus, setPublicationStatus] = useState<PublicationStatus>('PUBLISHED')
    const [resourceTypes, setResourceTypes] = useState<ResourceTypeOption[]>([])
    const [providers, setProviders] = useState<ProviderOption[]>([])
    const [gradeLevels, setGradeLevels] = useState<GradeLevelOption[]>([])
    const [subjects, setSubjects] = useState<SubjectOption[]>([])
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState('')
    const { mutate, isLoading, error, data, reset } = useUpdateResourceMetadata()

    const selectedResourceType = resourceTypes.find((item) => item.code === resourceTypeCode)
    const selectedProvider = providers.find((item) => item.id === providerId)
    const availableSubjects = useMemo(
        () => subjects.filter(
            (subject) => !gradeLevelId ||
                subject.grade_level_id === null ||
                subject.grade_level_id === gradeLevelId,
        ),
        [gradeLevelId, subjects],
    )

    useEffect(() => {
        if (!resourceId) {
            setLoadError('Thiếu mã học liệu.')
            setLoading(false)
            return
        }

        const id = resourceId
        const controller = new AbortController()

        async function loadData() {
            try {
                const encodedId = encodeURIComponent(id)
                const [resourceResponse, resourceTypeItems, providerItems, gradeItems, subjectItems] =
                    await Promise.all([
                        fetch(`/api/v1/resources/${encodedId}`, { signal: controller.signal }),
                        getResourceTypes(controller.signal),
                        getProviders(controller.signal),
                        getGradeLevels(controller.signal),
                        getSubjects(controller.signal),
                    ])

                if (!resourceResponse.ok) {
                    throw new Error(
                        resourceResponse.status === 404
                            ? 'Không tìm thấy học liệu.'
                            : `Không tải được học liệu (HTTP ${resourceResponse.status}).`,
                    )
                }

                const resource: ResourceDetailResponse = await resourceResponse.json()
                setTitle(resource.title)
                setResourceTypeCode(resource.resource_type_code)
                setProviderId(resource.provider_id)
                setGradeLevelId(resource.grade_levels[0]?.id ?? '')
                setSubjectId(resource.subjects[0]?.id ?? '')
                setPublicationStatus(resource.publication_status)
                setResourceTypes(resourceTypeItems)
                setProviders(providerItems)
                setGradeLevels(gradeItems)
                setSubjects(subjectItems)
            } catch (cause) {
                if (!controller.signal.aborted) {
                    setLoadError(
                        cause instanceof Error
                            ? cause.message
                            : 'Không thể tải dữ liệu cập nhật học liệu.',
                    )
                }
            } finally {
                if (!controller.signal.aborted) setLoading(false)
            }
        }

        void loadData()
        return () => controller.abort()
    }, [resourceId])

    useEffect(() => () => reset(), [reset])

    const detailPath = `/library/${encodeURIComponent(resourceId ?? '')}`

    useEffect(() => {
        if (!data) return

        const timer = window.setTimeout(() => {
            navigate(detailPath)
        }, 2500)

        return () => window.clearTimeout(timer)
    }, [data, detailPath, navigate])

    async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
        event.preventDefault()
        if (!resourceId || !selectedResourceType || !selectedProvider) return

        const payload: UpdateResourceMetadataRequest = {
            title: title.trim(),
            resource_type_code: selectedResourceType.code,
            resource_type_name_vi: selectedResourceType.name,
            provider_id: selectedProvider.id,
            provider_name: selectedProvider.name,
            provider_type: selectedProvider.provider_type,
            subject_id: subjectId || null,
            grade_level_id: gradeLevelId || null,
            publication_status: publicationStatus,
        }

        await mutate(resourceId, payload)
    }

    const formDisabled = loading || isLoading || Boolean(loadError)

    return (
        <section className={styles.page}>
            <Link className={styles.secondaryButton} to={detailPath}>
                ← Quay lại chi tiết
            </Link>

            <header className={styles.pageHeader}>
                <h1>Cập nhật học liệu</h1>
                <p className={styles.lead}>
                    Cập nhật tên, loại, đơn vị cung cấp, môn học, khối lớp và trạng thái.
                </p>
            </header>

            {loading ? (
                <p role="status">Đang tải dữ liệu học liệu...</p>
            ) : loadError ? (
                <p className={styles.alert} role="alert">{loadError}</p>
            ) : (
                <form className={`${styles.card} ${styles.form}`} onSubmit={handleSubmit}>
                    <div className={styles.field}>
                        <label htmlFor="title">Tên học liệu</label>
                        <input
                            id="title"
                            name="title"
                            value={title}
                            onChange={(event) => setTitle(event.target.value)}
                            maxLength={500}
                            required
                            disabled={formDisabled}
                        />
                    </div>

                    <div className={styles.field}>
                        <label htmlFor="resourceType">Loại học liệu</label>
                        <select
                            id="resourceType"
                            value={resourceTypeCode}
                            onChange={(event) => setResourceTypeCode(event.target.value)}
                            required
                            disabled={formDisabled}
                        >
                            <option value="">Chọn loại học liệu</option>
                            {resourceTypes.map((item) => (
                                <option key={item.code} value={item.code}>{item.name}</option>
                            ))}
                        </select>
                    </div>

                    <div className={styles.field}>
                        <label htmlFor="provider">Đơn vị cung cấp</label>
                        <select
                            id="provider"
                            value={providerId}
                            onChange={(event) => setProviderId(event.target.value)}
                            required
                            disabled={formDisabled}
                        >
                            <option value="">Chọn đơn vị cung cấp</option>
                            {providers.map((item) => (
                                <option key={item.id} value={item.id}>{item.name}</option>
                            ))}
                        </select>
                    </div>

                    <div className={styles.field}>
                        <label htmlFor="gradeLevel">Khối lớp</label>
                        <select
                            id="gradeLevel"
                            value={gradeLevelId}
                            onChange={(event) => {
                                const nextGradeId = event.target.value
                                setGradeLevelId(nextGradeId)
                                const currentSubject = subjects.find((item) => item.id === subjectId)
                                if (currentSubject?.grade_level_id && currentSubject.grade_level_id !== nextGradeId) {
                                    setSubjectId('')
                                }
                            }}
                            disabled={formDisabled}
                        >
                            <option value="">Không chọn khối lớp</option>
                            {gradeLevels.map((item) => (
                                <option key={item.id} value={item.id}>{item.grade_name_vi}</option>
                            ))}
                        </select>
                    </div>

                    <div className={styles.field}>
                        <label htmlFor="subject">Môn học</label>
                        <select
                            id="subject"
                            value={subjectId}
                            onChange={(event) => setSubjectId(event.target.value)}
                            disabled={formDisabled}
                        >
                            <option value="">Không chọn môn học</option>
                            {availableSubjects.map((item) => (
                                <option key={item.id} value={item.id}>{item.name}</option>
                            ))}
                        </select>
                    </div>

                    <div className={styles.field}>
                        <label htmlFor="publicationStatus">Trạng thái xuất bản</label>
                        <select
                            id="publicationStatus"
                            value={publicationStatus}
                            onChange={(event) => setPublicationStatus(event.target.value as PublicationStatus)}
                            disabled={formDisabled}
                        >
                            <option value="PUBLISHED">Đã xuất bản</option>
                            <option value="DRAFT">Bản nháp</option>
                            <option value="UNPUBLISHED">Chưa xuất bản</option>
                        </select>
                    </div>

                    {error && <p className={styles.alert} role="alert">{error}</p>}
                    {data && <p role="status">Cập nhật học liệu thành công!</p>}

                    <div className={styles.actions}>
                        <button
                            className={styles.primaryButton}
                            type="submit"
                            disabled={formDisabled || !title.trim() || !selectedResourceType || !selectedProvider}
                        >
                            {isLoading ? 'Đang lưu...' : 'Lưu thay đổi'}
                        </button>
                        <button className={styles.secondaryButton} type="button" onClick={() => navigate(detailPath)}>
                            Hủy
                        </button>
                    </div>
                </form>
            )}
        </section>
    )
}

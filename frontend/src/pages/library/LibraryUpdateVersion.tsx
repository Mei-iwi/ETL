import { CatalogNotice } from '../../components/CatalogNotice'
import { ProcessingPreferences } from '../../components/ProcessingPreferences'
import { defaultProcessingPreferences, getMediaFileType, saveProcessingPreferences } from '../../services/processingPreferences'
import { useEffect, useMemo, useRef, useState } from 'react'
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
import { startProcessing } from '../../services/processingApi'
import { createResourceVersion, metadataReadOnlyMessage } from '../../services/resourceApi'
import {
    getResourceSourceWriteBackStatus,
    type SourceWriteBackStatus,
} from '../../services/reconciliationApi'
import type {
    PublicationStatus,
    UpdateResourceMetadataRequest,
} from '../../types/resourceMetadata'
import styles from './LibraryPages.module.css'
import { LIBRARY_FILE_ACCEPT, LIBRARY_FILE_FORMATS, libraryFileError } from './libraryFileFormats'
import { SelectedMediaPreview } from './MediaFilePreview'

type ResourceDetailResponse = {
    source_origin?: string | null
    title: string
    resource_type_code: string
    provider_id: string
    publication_status: PublicationStatus
    subjects: { id: string }[]
    grade_levels: { id: string }[]
}

type UpdateStage = 'idle' | 'metadata' | 'uploading' | 'processing'

export function LibraryUpdateVersion() {
    const [processingPreferences, setProcessingPreferences] = useState(defaultProcessingPreferences)
    const [replacementFileName, setReplacementFileName] = useState('')
    const [replacementFile, setReplacementFile] = useState<File | null>(null)
    const selectedMediaType = getMediaFileType(replacementFileName)
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
    const [sourceOrigin, setSourceOrigin] = useState<string | null>(null)
    const [sourceWritebackStatus, setSourceWritebackStatus] = useState<SourceWriteBackStatus | null>(null)
    const [loadError, setLoadError] = useState('')
    const [updateStage, setUpdateStage] = useState<UpdateStage>('idle')
    const [versionError, setVersionError] = useState('')
    const [versionMessage, setVersionMessage] = useState('')
    const [returnToDetail, setReturnToDetail] = useState(false)
    const submittingRef = useRef(false)
    const originalMetadata = useRef<Partial<UpdateResourceMetadataRequest>>({})
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
                if (controller.signal.aborted) return
                setSourceOrigin(resource.source_origin ?? null)
                setSourceWritebackStatus(null)
                if (resource.source_origin === 'POSTGRESQL') {
                    try {
                        setSourceWritebackStatus(await getResourceSourceWriteBackStatus(id, controller.signal))
                    } catch {
                        // A missing capability is rendered as read-only; the
                        // form must never infer write permission from the UI.
                        setSourceWritebackStatus(null)
                    }
                }
                setTitle(resource.title)
                setResourceTypeCode(resource.resource_type_code)
                setProviderId(resource.provider_id)
                setGradeLevelId(resource.grade_levels[0]?.id ?? '')
                setSubjectId(resource.subjects[0]?.id ?? '')
                setPublicationStatus(resource.publication_status)
                originalMetadata.current = {
                    title: resource.title, resource_type_code: resource.resource_type_code,
                    provider_id: resource.provider_id, publication_status: resource.publication_status,
                    subject_id: resource.subjects[0]?.id ?? null,
                    grade_level_id: resource.grade_levels[0]?.id ?? null,
                }
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
        if ((!data && !versionMessage) || !returnToDetail) return

        const timer = window.setTimeout(() => {
            navigate(detailPath)
        }, 2500)

        return () => window.clearTimeout(timer)
    }, [data, detailPath, navigate, returnToDetail, versionMessage])

    async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
        event.preventDefault()
        if (submittingRef.current || !resourceId || !selectedResourceType || !selectedProvider) return

        const formData = new FormData(event.currentTarget)
        const fileValue = formData.get('file')
        const file = fileValue instanceof File && fileValue.size > 0 ? fileValue : null
        const selectedFile = fileValue instanceof File && fileValue.name ? fileValue : null
        const fileError = selectedFile ? libraryFileError(selectedFile) : null
        if (fileError) { setVersionError(fileError); return }

        if (sourceOrigin === 'POSTGRESQL') {
            if (!sourceWritebackTitleOnly) {
                setVersionError('Học liệu thuộc SOURCE và chưa được cấp quyền write-back. Vui lòng cập nhật tại SOURCE.')
                return
            }
            if (selectedFile) {
                setVersionError('Biểu mẫu này cập nhật thông tin học liệu, không thay đổi tệp.')
                return
            }
            submittingRef.current = true
            setUpdateStage('metadata')
            setVersionError('')
            setVersionMessage('')
            try {
                const values: Partial<UpdateResourceMetadataRequest> = {
                    title: title.trim(), resource_type_code: selectedResourceType.code,
                    provider_id: selectedProvider.id, publication_status: publicationStatus,
                    subject_id: subjectId || null, grade_level_id: gradeLevelId || null,
                }
                const changes = Object.fromEntries(Object.entries(values).filter(
                    ([name, value]) => originalMetadata.current[name as keyof UpdateResourceMetadataRequest] !== value,
                ))
                if (!Object.keys(changes).length) throw new Error('Thông tin học liệu chưa thay đổi.')
                const response = await fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/metadata`, {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(changes),
                })
                if (!response.ok) {
                    const body = await response.json().catch(() => null)
                    throw new Error(body?.detail?.message ?? body?.detail?.code ?? `HTTP ${response.status}`)
                }
                setVersionMessage('Đã tiếp nhận cập nhật metadata. Trạng thái đồng bộ được xử lý theo worker.')
                setReturnToDetail(true)
            } catch (cause) {
                setVersionError(cause instanceof Error ? cause.message : 'Không thể cập nhật tại SOURCE.')
            } finally {
                submittingRef.current = false
                setUpdateStage('idle')
            }
            return
        }

        const readOnly = metadataReadOnlyMessage(sourceOrigin)
        if (readOnly) { setVersionError(readOnly); return }

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

        submittingRef.current = true
        setUpdateStage('metadata')
        setVersionError('')
        setVersionMessage('')
        setReturnToDetail(false)
        let metadataUpdated = false

        try {
            await mutate(resourceId, payload)
            metadataUpdated = true

            if (!file) {
                setVersionMessage('Cập nhật thông tin học liệu thành công.')
                setReturnToDetail(true)
                return
            }

            setUpdateStage('uploading')
            const version = await createResourceVersion(resourceId, file)
            if (!version.resource_version_id) {
                throw new Error('Phản hồi tạo phiên bản thiếu mã phiên bản')
            }

            if (version.action === 'NO_CHANGE') {
                setVersionMessage('Tệp không thay đổi, không cần xử lý lại.')
                setReturnToDetail(true)
                return
            }

            setUpdateStage('processing')
            try {
                await saveProcessingPreferences(version.resource_version_id, getMediaFileType(file.name) ? processingPreferences : defaultProcessingPreferences)
                const processResult = await startProcessing(version.resource_version_id)
                if (!processResult.job_id) throw new Error('Phản hồi xử lý thiếu mã job')
                navigate(
                    `/library/processing?versionId=${encodeURIComponent(version.resource_version_id)}&jobId=${encodeURIComponent(processResult.job_id)}&resourceId=${encodeURIComponent(resourceId)}`,
                    { replace: true },
                )
            } catch (processError) {
                const detail = processError instanceof Error ? `: ${processError.message}` : ''
                navigate(
                    `/library/processing?versionId=${encodeURIComponent(version.resource_version_id)}&resourceId=${encodeURIComponent(resourceId)}`,
                    {
                        replace: true,
                        state: {
                            processingNotice: `Đã tạo phiên bản mới, chưa xác nhận được yêu cầu xử lý${detail}`,
                        },
                    },
                )
            }
        } catch (cause) {
            if (metadataUpdated) {
                setVersionError(
                    cause instanceof Error
                        ? `Đã cập nhật metadata nhưng không thể cập nhật tệp: ${cause.message}`
                        : 'Đã cập nhật metadata nhưng không thể cập nhật tệp.',
                )
            }
        } finally {
            submittingRef.current = false
            setUpdateStage('idle')
        }
    }

    const sourceWritebackTitleOnly = sourceOrigin === 'POSTGRESQL'
        && sourceWritebackStatus?.enabled === true
        && sourceWritebackStatus.writer_supported === true
        && sourceWritebackStatus.fields.includes('title')
        && typeof sourceWritebackStatus.expected_source_state_hash === 'string'
    const ownershipMessage = sourceWritebackTitleOnly
        ? 'Cập nhật thông tin học liệu và đồng bộ về SOURCE.'
        : metadataReadOnlyMessage(sourceOrigin)
    const sourceFieldsLocked = sourceOrigin === 'POSTGRESQL'
    const fieldLocked = (field: string) => sourceFieldsLocked && !sourceWritebackStatus?.fields.includes(field)
    const formDisabled = loading || isLoading || updateStage !== 'idle' || Boolean(loadError)
        || (Boolean(ownershipMessage) && !sourceWritebackTitleOnly)
    const submitLabel = updateStage === 'metadata'
        ? 'Đang lưu thông tin...'
        : updateStage === 'uploading'
            ? 'Đang tạo phiên bản mới...'
            : updateStage === 'processing'
                ? 'Đang khởi chạy xử lý...'
                : 'Lưu và xử lý thay đổi'

    return (
        <section className={styles.page}>
            <Link className={styles.backLink} to="/library/list">
                ← Quay lại danh sách học liệu
            </Link>

            <header className={styles.pageHeader}>
                <h1>Cập nhật học liệu</h1>
            </header>

            {loading ? (
                <p role="status">Đang tải dữ liệu học liệu...</p>
            ) : loadError ? (
                <p className={styles.alert} role="alert">{loadError}</p>
            ) : (
                <form className={`${styles.card} ${styles.form}`} onSubmit={handleSubmit}>
                <CatalogNotice providers={providers} types={resourceTypes} />
                    {ownershipMessage && <p className={styles.notice} role="status">{ownershipMessage}</p>}
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
                            disabled={formDisabled || fieldLocked('resource_type_id')}
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
                            disabled={formDisabled || fieldLocked('provider_id')}
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
                            disabled={formDisabled || fieldLocked('grade_level_id')}
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
                            disabled={formDisabled || fieldLocked('subject_id')}
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
                            disabled={formDisabled || fieldLocked('publication_status')}
                        >
                            <option value="PUBLISHED">Đã xuất bản</option>
                            <option value="DRAFT">Bản nháp</option>
                            {sourceOrigin === 'POSTGRESQL'
                                ? <option value="ARCHIVED">Lưu trữ</option>
                                : <option value="UNPUBLISHED">Chưa xuất bản</option>}
                        </select>
                    </div>

                    <div className={styles.field}>
                        <label htmlFor="file">Thay tệp học liệu (không bắt buộc)</label>
                        <input
                            id="file"
                            name="file"
                            type="file"
                            accept={LIBRARY_FILE_ACCEPT}
                            onChange={event => {
                                const chosen = event.currentTarget.files?.[0] ?? null
                                const error = chosen ? libraryFileError(chosen) : null
                                setVersionError(error ?? '')
                                setReplacementFile(error ? null : chosen)
                                setReplacementFileName(error ? '' : chosen?.name ?? '')
                                setProcessingPreferences(defaultProcessingPreferences)
                            }}
                            disabled={formDisabled || sourceFieldsLocked}
                        />
                        <small>{LIBRARY_FILE_FORMATS}</small>
                    </div>

                    <SelectedMediaPreview file={replacementFile} />
                    {selectedMediaType && <ProcessingPreferences
                        value={processingPreferences}
                        onChange={setProcessingPreferences}
                        disabled={formDisabled || sourceFieldsLocked}
                        mediaType={selectedMediaType}
                    />}

                    {error && <p className={styles.alert} role="alert">{error}</p>}
                    {versionError && <p className={styles.alert} role="alert">{versionError}</p>}
                    {(data || versionMessage) && updateStage === 'idle' && (
                        <p role="status" className={styles.success}>
                            {versionMessage || 'Cập nhật học liệu thành công!'}
                        </p>
                    )}

                    <div className={styles.actions}>
                        <button
                            className={styles.primaryButton}
                            type="submit"
                            disabled={formDisabled || !title.trim() || !selectedResourceType || !selectedProvider}
                        >
                            {submitLabel}
                        </button>
                        <button className={styles.secondaryButton} type="button" onClick={() => navigate('/library/list')} disabled={isLoading || updateStage !== 'idle'}>
                            Hủy
                        </button>
                    </div>
                </form>
            )}
        </section>
    )
}

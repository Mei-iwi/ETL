import React, { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router"
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
import styles from './LibraryPages.module.css'

type CreateResourceRespones = {
    id: string
    title: string
    resource_type_code: string
    media_category: string
    mime_type: string
    file_name: string
    file_size: number
    source_hash: string
    initial_version_id: string
    version_number: number
    lifecycle_status: string,
    created_at: string
}

type SubmitStage = 'idle' | 'creating' | 'processing'

export function LibraryCreatePage() {
    const navigate = useNavigate()
    const submittingRef = useRef(false)

    const [submitStage, setSubmitStage] = useState<SubmitStage>('idle')
    const [error, setError] = useState('')
    const [resourceTypes, setResourceTypes] = useState<ResourceTypeOption[]>([])
    const [providers, setProviders] = useState<ProviderOption[]>([])
    const [resourceTypeCode, setResourceTypeCode] = useState('')
    const [providerId, setProviderId] = useState('')
    const [catalogLoading, setCatalogLoading] = useState(true)
    const [catalogError, setCatalogError] = useState('')
    const [gradeLevels, setGradeLevels] = useState<GradeLevelOption[]>([])
    const [subjects, setSubjects] = useState<SubjectOption[]>([])
    const [gradeLevelId, setGradeLevelId] = useState('')
    const [subjectId, setSubjectId] = useState('')
    const [metadataLoading, setMetadataLoading] = useState(true)
    const [metadataError, setMetadataError] = useState('')
    const [catalogRequest, setCatalogRequest] = useState(0)

    const selectedProvider = providers.find((provider) => provider.id === providerId)
    const availableSubjects = subjects.filter(
        (subject) => !gradeLevelId ||
            subject.grade_level_id === null ||
            subject.grade_level_id === gradeLevelId,
    )

    useEffect(() => {
        const controller = new AbortController()

        async function loadCatalogs() {
            try {
                const [resourceTypeItems, providerItems] = await Promise.all([
                    getResourceTypes(controller.signal),
                    getProviders(controller.signal),
                ])
                if (controller.signal.aborted) return

                setResourceTypes(resourceTypeItems)
                setProviders(providerItems)
                if (resourceTypeItems.length === 0 || providerItems.length === 0) {
                    setCatalogError('Danh mục loại học liệu hoặc nhà cung cấp đang trống.')
                }
            } catch (e) {
                if (controller.signal.aborted) return
                setResourceTypes([])
                setProviders([])
                setCatalogError(
                    e instanceof Error ? e.message : 'Không thể tải danh mục học liệu',
                )
            } finally {
                if (!controller.signal.aborted) setCatalogLoading(false)
            }
        }

        void loadCatalogs()
        return () => controller.abort()
    }, [catalogRequest])

    useEffect(() => {
        const controller = new AbortController()

        async function loadMetadataCatalogs() {
            try {
                const [gradeLevelItems, subjectItems] = await Promise.all([
                    getGradeLevels(controller.signal),
                    getSubjects(controller.signal),
                ])
                if (controller.signal.aborted) return
                setGradeLevels(gradeLevelItems)
                setSubjects(subjectItems)
            } catch (e) {
                if (controller.signal.aborted) return
                setGradeLevels([])
                setSubjects([])
                setMetadataError(
                    e instanceof Error ? e.message : 'Không thể tải danh mục môn học và khối lớp',
                )
            } finally {
                if (!controller.signal.aborted) setMetadataLoading(false)
            }
        }

        void loadMetadataCatalogs()
        return () => controller.abort()
    }, [catalogRequest])

    function handleReloadCatalogs() {
        setResourceTypeCode('')
        setProviderId('')
        setGradeLevelId('')
        setSubjectId('')
        setCatalogError('')
        setMetadataError('')
        setCatalogLoading(true)
        setMetadataLoading(true)
        setCatalogRequest((current) => current + 1)
    }

    async function handleSubmit(
        event: React.FormEvent<HTMLFormElement>,
    ) {
        event.preventDefault()

        if (submittingRef.current) return

        if (!resourceTypeCode || !selectedProvider || catalogLoading || catalogError) {
            setError('Vui lòng chọn loại học liệu và nhà cung cấp hợp lệ.')
            return
        }

        submittingRef.current = true
        setSubmitStage('creating')
        setError('')

        const form = event.currentTarget
        const formData = new FormData(form)

        try {
            const response = await fetch('/api/v1/resources', {
                method: 'POST',
                body: formData
            })

            if (!response.ok) {
                const body = await response.json().catch(() => null)

                throw new Error(
                    body?.detail?.message ?? body?.detail ?? `HTTP ${response.status}`
                )
            }
            const createdResource: CreateResourceRespones = await response.json()
            const versionId = createdResource.initial_version_id
            if (typeof versionId !== 'string' || !versionId.trim()) {
                throw new Error('Phản hồi tạo học liệu thiếu mã phiên bản')
            }

            try {
                setSubmitStage('processing')
                const processResult = await startProcessing(versionId)
                if (typeof processResult.job_id !== 'string' || !processResult.job_id.trim()) {
                    throw new Error('Phản hồi xử lý thiếu mã job')
                }
                navigate(
                    `/library/processing?versionId=${encodeURIComponent(versionId)}&jobId=${encodeURIComponent(processResult.job_id)}`,
                    { replace: true },
                )
            } catch (processError) {
                const detail = processError instanceof Error ? `: ${processError.message}` : ''
                navigate(
                    `/library/processing?versionId=${encodeURIComponent(versionId)}`,
                    {
                        replace: true,
                        state: {
                            processingNotice: `Đã tạo học liệu, chưa xác nhận được yêu cầu xử lý${detail}`,
                        },
                    },
                )
            }
        }
        catch (e) {
            setError(
                e instanceof Error ? e.message : 'Không thể tạo học liệu',
            )
        } finally {
            submittingRef.current = false
            setSubmitStage('idle')
        }
    }

    return (
        <section className={styles.page}>
            <header className={styles.pageHeader}>
                <h1>Thêm học liệu</h1>
            </header>
            <form className={`${styles.card} ${styles.form}`} onSubmit={handleSubmit}>

                <div className={styles.field}>
                    <label htmlFor="title">Tên học liệu</label>
                    <input type="text" id="title" name="title" required />
                </div>
                <div className={styles.field}>
                    <label htmlFor="resourceType">Loại học liệu</label>
                    <select
                        id="resourceType"
                        name="resource_type_code"
                        value={resourceTypeCode}
                        onChange={(event) => setResourceTypeCode(event.target.value)}
                        disabled={catalogLoading || Boolean(catalogError)}
                        required
                    >
                        <option value="">Chọn loại học liệu</option>
                        {resourceTypes.map((resourceType) => (
                            <option key={resourceType.code} value={resourceType.code}>
                                {resourceType.name}
                            </option>
                        ))}
                    </select>
                </div>
                <div className={styles.field}>
                    <label htmlFor="providerId">Nhà cung cấp</label>
                    <select
                        id="providerId"
                        name="provider_id"
                        value={providerId}
                        onChange={(event) => setProviderId(event.target.value)}
                        disabled={catalogLoading || Boolean(catalogError)}
                        required
                    >
                        <option value="">Chọn nhà cung cấp</option>
                        {providers.map((provider) => (
                            <option key={provider.id} value={provider.id}>
                                {provider.name}
                            </option>
                        ))}
                    </select>
                    <input type="hidden" name="provider_name" value={selectedProvider?.name ?? ''} />
                    <input type="hidden" name="provider_type" value={selectedProvider?.provider_type ?? ''} />
                </div>
                <div className={styles.field}>
                    <label htmlFor="file">Tải lên tệp học liệu</label>
                    <input id='file' type="file" name="file" accept="application/pdf,.pdf" required />
                </div>
                <div className={styles.field}>
                    <label htmlFor="gradeLevel">Khối lớp</label>
                    <select
                        id="gradeLevel"
                        name="grade_level_id"
                        value={gradeLevelId}
                        onChange={(event) => {
                            const nextGradeLevelId = event.target.value
                            setGradeLevelId(nextGradeLevelId)
                            const selectedSubject = subjects.find((subject) => subject.id === subjectId)
                            if (
                                selectedSubject?.grade_level_id &&
                                selectedSubject.grade_level_id !== nextGradeLevelId
                            ) {
                                setSubjectId('')
                            }
                        }}
                        disabled={metadataLoading || Boolean(metadataError)}
                    >
                        <option value="">Không chọn khối lớp</option>
                        {gradeLevels.map((gradeLevel) => (
                            <option key={gradeLevel.id} value={gradeLevel.id}>
                                {gradeLevel.grade_name_vi}
                            </option>
                        ))}
                    </select>
                </div>
                <div className={styles.field}>
                    <label htmlFor="subject">Môn học</label>
                    <select
                        id="subject"
                        name="subject_id"
                        value={subjectId}
                        onChange={(event) => setSubjectId(event.target.value)}
                        disabled={metadataLoading || Boolean(metadataError)}
                    >
                        <option value="">Không chọn môn học</option>
                        {availableSubjects.map((subject) => (
                            <option key={subject.id} value={subject.id}>
                                {subject.name}
                            </option>
                        ))}
                    </select>
                </div>
                <div className={styles.field}>
                    <label htmlFor="publicationStatus">Trạng thái xuất bản</label>
                    <select id="publicationStatus" name="publication_status" defaultValue="PUBLISHED">
                        <option value="PUBLISHED">Đã xuất bản</option>
                        <option value="DRAFT">Bản nháp</option>
                        <option value="UNPUBLISHED">Chưa xuất bản</option>
                    </select>
                </div>
                {(catalogLoading || metadataLoading) && <p role="status">Đang tải danh mục...</p>}
                {catalogError && (
                    <p className={styles.alert} role="alert">{catalogError}</p>
                )}
                {metadataError && (
                    <p className={styles.alert} role="alert">{metadataError}</p>
                )}
                {(catalogError || metadataError) && (
                    <div className={styles.actions}>
                        <button className={styles.primaryButton} type="button" onClick={handleReloadCatalogs}>
                            Tải lại danh mục
                        </button>
                    </div>
                )}
                {error && (
                    <p className={styles.alert} role="alert">{error}</p>
                )}
                <div className={styles.actions}>
                    <button
                        className={styles.primaryButton}
                        type="submit"
                        disabled={submitStage !== 'idle' || catalogLoading || Boolean(catalogError) || !resourceTypeCode || !selectedProvider}
                    >
                        {submitStage === 'creating'
                            ? 'Đang tạo học liệu...'
                            : submitStage === 'processing'
                                ? 'Đang khởi chạy xử lý...'
                                : 'Tạo và xử lý học liệu'}
                    </button>
                </div>
            </form>
        </section>
    )
}

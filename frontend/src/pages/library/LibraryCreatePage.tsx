import React, { useEffect, useRef, useState } from "react"
import { ProcessingPreferences } from '../../components/ProcessingPreferences'
import { defaultProcessingPreferences, getMediaFileType, saveProcessingPreferences } from '../../services/processingPreferences'
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
} from "../../services/libraryLookupApi"
import { startProcessing } from "../../services/processingApi"
import styles from "./LibraryCreatePages.module.css"
import { LIBRARY_FILE_ACCEPT, LIBRARY_FILE_FORMATS, libraryFileError } from './libraryFileFormats'
import { SelectedMediaPreview } from './MediaFilePreview'

type CreateResourceResponse = {
    id: string
    title: string
    initial_version_id: string
}

type SubmitStage = "idle" | "creating" | "processing"

function formatFileSize(size: number): string {
    if (size < 1024) return `${size} B`
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
    return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

export function LibraryCreatePage() {
    const [processingPreferences, setProcessingPreferences] = useState(defaultProcessingPreferences)
    const navigate = useNavigate()
    const submittingRef = useRef(false)
    const fileInputRef = useRef<HTMLInputElement>(null)

    const [submitStage, setSubmitStage] = useState<SubmitStage>("idle")
    const [error, setError] = useState("")

    const [resourceTypes, setResourceTypes] = useState<ResourceTypeOption[]>([])
    const [providers, setProviders] = useState<ProviderOption[]>([])
    const [gradeLevels, setGradeLevels] = useState<GradeLevelOption[]>([])
    const [subjects, setSubjects] = useState<SubjectOption[]>([])

    const [resourceTypeCode, setResourceTypeCode] = useState("")
    const [providerId, setProviderId] = useState("")
    const [gradeLevelId, setGradeLevelId] = useState("")
    const [subjectId, setSubjectId] = useState("")

    const [catalogLoading, setCatalogLoading] = useState(true)
    const [catalogError, setCatalogError] = useState("")
    const [metadataLoading, setMetadataLoading] = useState(true)
    const [metadataError, setMetadataError] = useState("")
    const [catalogRequest, setCatalogRequest] = useState(0)

    const [selectedFile, setSelectedFile] = useState<File | null>(null)
    const selectedMediaType = getMediaFileType(selectedFile?.name)
    const [isDragging, setIsDragging] = useState(false)

    const selectedProvider = providers.find(
        (provider) => provider.id === providerId,
    )

    const availableSubjects = subjects.filter(
        (subject) =>
            !gradeLevelId ||
            subject.grade_level_id == null ||
            subject.grade_level_id === gradeLevelId,
    )

    const isSubmitting = submitStage !== "idle"

    useEffect(() => {
        const controller = new AbortController()

        async function loadCatalogs() {
            setCatalogLoading(true)
            setCatalogError("")

            try {
                const [types, providerItems] = await Promise.all([
                    getResourceTypes(controller.signal),
                    getProviders(controller.signal),
                ])

                if (controller.signal.aborted) return

                setResourceTypes(types)
                setProviders(providerItems)

                if (!types.length || !providerItems.length) {
                    setCatalogError(
                        "Danh mục loại học liệu hoặc nhà cung cấp đang trống.",
                    )
                }
            } catch (e) {
                if (controller.signal.aborted) return

                setResourceTypes([])
                setProviders([])
                setCatalogError(
                    e instanceof Error
                        ? e.message
                        : "Không thể tải danh mục học liệu.",
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

        async function loadMetadata() {
            setMetadataLoading(true)
            setMetadataError("")

            try {
                const [grades, subjectItems] = await Promise.all([
                    getGradeLevels(controller.signal),
                    getSubjects(controller.signal),
                ])

                if (controller.signal.aborted) return

                setGradeLevels(grades)
                setSubjects(subjectItems)
            } catch (e) {
                if (controller.signal.aborted) return

                setGradeLevels([])
                setSubjects([])
                setMetadataError(
                    e instanceof Error
                        ? e.message
                        : "Không thể tải danh mục môn học và khối lớp.",
                )
            } finally {
                if (!controller.signal.aborted) setMetadataLoading(false)
            }
        }

        void loadMetadata()
        return () => controller.abort()
    }, [catalogRequest])

    function handleReloadCatalogs() {
        setResourceTypeCode("")
        setProviderId("")
        setGradeLevelId("")
        setSubjectId("")
        setCatalogRequest((current) => current + 1)
    }

    function applySelectedFile(file?: File) {
        if (!file) return

        const fileError = libraryFileError(file)
        if (fileError) {
            setError(fileError)
            return
        }

        setSelectedFile(file)
        setProcessingPreferences(defaultProcessingPreferences)
        setError("")
    }

    function handleFileDrop(
        event: React.DragEvent<HTMLLabelElement>,
    ) {
        event.preventDefault()
        setIsDragging(false)
        applySelectedFile(event.dataTransfer.files?.[0])
    }

    function handleFileInputChange(
        event: React.ChangeEvent<HTMLInputElement>,
    ) {
        applySelectedFile(event.target.files?.[0])
    }

    function handleRemoveFile() {
        if (fileInputRef.current) {
            fileInputRef.current.value = ""
        }

        setSelectedFile(null)
        setError("")
    }

    async function handleSubmit(
        event: React.FormEvent<HTMLFormElement>,
    ) {
        event.preventDefault()

        if (submittingRef.current) return

        if (catalogLoading || catalogError || !resourceTypeCode || !selectedProvider) {
            setError("Vui lòng kiểm tra loại học liệu và nhà cung cấp.")
            return
        }

        if (metadataLoading || metadataError) {
            setError("Danh mục môn học hoặc khối lớp chưa sẵn sàng.")
            return
        }

        if (!selectedFile) {
            setError("Vui lòng chọn tệp học liệu trước khi tiếp tục.")
            return
        }

        submittingRef.current = true
        setSubmitStage("creating")
        setError("")

        const formData = new FormData(event.currentTarget)
        // The picker unmounts while the selected-file card is visible.
        // State retains the File for both picker and drag/drop selection.
        formData.set("file", selectedFile, selectedFile.name)

        try {
            const response = await fetch("/api/v1/resources", {
                method: "POST",
                body: formData,
            })

            if (!response.ok) {
                const body = await response.json().catch(() => null)
                const detail = body?.detail
                const message =
                    typeof detail === "string"
                        ? detail
                        : detail?.message ?? `HTTP ${response.status}`

                throw new Error(message)
            }

            const created: CreateResourceResponse = await response.json()
            const versionId = created.initial_version_id

            if (!versionId) {
                throw new Error("Phản hồi tạo học liệu thiếu mã phiên bản.")
            }

            setSubmitStage("processing")

            try {
                await saveProcessingPreferences(versionId, selectedMediaType ? processingPreferences : defaultProcessingPreferences)
                const result = await startProcessing(versionId)

                if (!result.job_id) {
                    throw new Error("Phản hồi xử lý thiếu mã job.")
                }

                navigate(
                    `/library/processing?versionId=${encodeURIComponent(versionId)}&jobId=${encodeURIComponent(result.job_id)}`,
                    { replace: true },
                )
            } catch (processingError) {
                const message =
                    processingError instanceof Error
                        ? processingError.message
                        : "Không xác nhận được yêu cầu xử lý."

                navigate(
                    `/library/processing?versionId=${encodeURIComponent(versionId)}`,
                    {
                        replace: true,
                        state: {
                            processingNotice:
                                `Đã tạo học liệu nhưng chưa xác nhận được yêu cầu xử lý: ${message}`,
                        },
                    },
                )
            }
        } catch (e) {
            setError(
                e instanceof Error
                    ? e.message
                    : "Không thể tạo học liệu.",
            )
        } finally {
            submittingRef.current = false
            setSubmitStage("idle")
        }
    }

    return (
        <section className={styles.page}>
            <header className={styles.pageHeader}>
                <h1>Thêm học liệu số</h1>
            </header>

            <form
                className={styles.form}
                onSubmit={handleSubmit}
                encType="multipart/form-data"
            >
                <div className={styles.formMain}>
                    {/* Thông tin học liệu */}
                    <section className={styles.card}>
                        <div className={styles.sectionHeader}>
                            <h2>Thông tin học liệu</h2>
                        </div>

                        <div className={styles.field}>
                            <label htmlFor="title">
                                Tên học liệu <span className={styles.required}>*</span>
                            </label>
                            <input
                                id="title"
                                name="title"
                                type="text"
                                placeholder="Ví dụ: Bài giảng Toán lớp 10 — Hàm số"
                                maxLength={300}
                                required
                            />
                        </div>

                        <div className={styles.fieldGrid}>
                            <div className={styles.field}>
                                <label htmlFor="resourceType">
                                    Loại học liệu <span className={styles.required}>*</span>
                                </label>
                                <select
                                    id="resourceType"
                                    name="resource_type_code"
                                    value={resourceTypeCode}
                                    onChange={(event) =>
                                        setResourceTypeCode(event.target.value)
                                    }
                                    disabled={catalogLoading || Boolean(catalogError)}
                                    required
                                >
                                    <option value="">Chọn loại học liệu</option>
                                    {resourceTypes.map((item) => (
                                        <option key={item.code} value={item.code}>
                                            {item.name}
                                        </option>
                                    ))}
                                </select>
                            </div>

                            <div className={styles.field}>
                                <label htmlFor="providerId">
                                    Nhà cung cấp <span className={styles.required}>*</span>
                                </label>
                                <select
                                    id="providerId"
                                    name="provider_id"
                                    value={providerId}
                                    onChange={(event) =>
                                        setProviderId(event.target.value)
                                    }
                                    disabled={catalogLoading || Boolean(catalogError)}
                                    required
                                >
                                    <option value="">Chọn nhà cung cấp</option>
                                    {providers.map((item) => (
                                        <option key={item.id} value={item.id}>
                                            {item.name}
                                        </option>
                                    ))}
                                </select>

                                <input
                                    type="hidden"
                                    name="provider_name"
                                    value={selectedProvider?.name ?? ""}
                                />
                                <input
                                    type="hidden"
                                    name="provider_type"
                                    value={selectedProvider?.provider_type ?? ""}
                                />
                            </div>
                        </div>
                    </section>


                    {/* Tệp học liệu */}
                    <section className={styles.card}>
                        <div className={styles.sectionHeader}>
                            <h2>Tệp học liệu</h2>
                        </div>

                        {!selectedFile ? (
                            <label
                                className={`${styles.uploadArea} ${isDragging ? styles.uploadAreaDragging : ""
                                    }`}
                                htmlFor="file"
                                onDragEnter={(event) => {
                                    event.preventDefault()
                                    setIsDragging(true)
                                }}
                                onDragOver={(event) => {
                                    event.preventDefault()
                                    setIsDragging(true)
                                }}
                                onDragLeave={(event) => {
                                    event.preventDefault()

                                    const next = event.relatedTarget

                                    if (
                                        !(next instanceof Node) ||
                                        !event.currentTarget.contains(next)
                                    ) {
                                        setIsDragging(false)
                                    }
                                }}
                                onDrop={handleFileDrop}
                            >
                                <span className={styles.uploadIcon} aria-hidden="true">
                                    <svg viewBox="0 0 24 24" fill="none">
                                        <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5" />
                                        <path d="M5 14.5v4A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-4" />
                                    </svg>
                                </span>

                                <strong>
                                    {isDragging
                                        ? "Thả tệp vào đây"
                                        : "Chọn tệp học liệu"}
                                </strong>

                                <span className={styles.uploadHint}>
                                    Bổ sung tệp - Học liệu
                                </span>

                                <span className={styles.fileTypes}>
                                    {LIBRARY_FILE_FORMATS}
                                </span>

                                <input
                                    ref={fileInputRef}
                                    className={styles.fileInput}
                                    id="file"
                                    type="file"
                                    name="file"
                                    accept={LIBRARY_FILE_ACCEPT}
                                    onChange={handleFileInputChange}
                                    required
                                />
                            </label>
                        ) : (
                            <div className={styles.selectedFile}>
                                <span
                                    className={styles.selectedFileIcon}
                                    aria-hidden="true"
                                >
                                    <svg viewBox="0 0 24 24" fill="none">
                                        <path d="M7 3.75h7l5 5v11.5H7a2 2 0 0 1-2-2v-12a2.5 2.5 0 0 1 2-2.5Z" />
                                        <path d="M14 3.75v5h5M8.5 13h6M8.5 16.5h6" />
                                    </svg>
                                </span>

                                <span className={styles.selectedFileInfo}>
                                    <strong title={selectedFile.name}>
                                        {selectedFile.name}
                                    </strong>
                                    <span>{formatFileSize(selectedFile.size)}</span>
                                </span>

                                <button
                                    type="button"
                                    className={styles.removeFileButton}
                                    aria-label="Bỏ tệp đã chọn"
                                    onClick={handleRemoveFile}
                                >
                                    ×
                                </button>
                            </div>
                        )}
                    </section>


                    {/* Metadata */}
                    <section className={styles.card}>
                        <div className={styles.sectionHeader}>
                            <h2>Metadata phục vụ truy vấn</h2>
                        </div>

                        <div className={styles.fieldGrid}>
                            <div className={styles.field}>
                                <label htmlFor="gradeLevel">Khối lớp</label>
                                <select
                                    id="gradeLevel"
                                    name="grade_level_id"
                                    value={gradeLevelId}
                                    onChange={(event) => {
                                        const nextGradeId = event.target.value
                                        setGradeLevelId(nextGradeId)

                                        const currentSubject = subjects.find(
                                            (subject) => subject.id === subjectId,
                                        )

                                        if (
                                            currentSubject?.grade_level_id &&
                                            currentSubject.grade_level_id !== nextGradeId
                                        ) {
                                            setSubjectId("")
                                        }
                                    }}
                                    disabled={metadataLoading || Boolean(metadataError)}
                                >
                                    <option value="">Không chọn khối lớp</option>
                                    {gradeLevels.map((item) => (
                                        <option key={item.id} value={item.id}>
                                            {item.grade_name_vi}
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
                                    onChange={(event) =>
                                        setSubjectId(event.target.value)
                                    }
                                    disabled={metadataLoading || Boolean(metadataError)}
                                >
                                    <option value="">Không chọn môn học</option>
                                    {availableSubjects.map((item) => (
                                        <option key={item.id} value={item.id}>
                                            {item.name}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        </div>

                        <div className={styles.field}>
                            <label htmlFor="publicationStatus">
                                Trạng thái xuất bản
                            </label>
                            <select
                                id="publicationStatus"
                                name="publication_status"
                                defaultValue="PUBLISHED"
                            >
                                <option value="PUBLISHED">Đã xuất bản</option>
                                <option value="DRAFT">Bản nháp</option>
                                <option value="UNPUBLISHED">Chưa xuất bản</option>
                            </select>
                        </div>
                    </section>

                    {/* Trạng thái danh mục */}
                    {(catalogLoading || metadataLoading || catalogError || metadataError) && <section className={styles.statusCard}>

                        <div className={styles.statusContent}>
                            <h2>Trạng thái danh mục</h2>

                            {catalogLoading || metadataLoading ? (
                                <p className={styles.statusDescription}>
                                    Đang kiểm tra các danh mục...
                                </p>
                            ) : catalogError || metadataError ? (
                                <>
                                    <p className={styles.statusError}>
                                        {catalogError || metadataError}
                                    </p>
                                    <button
                                        type="button"
                                        className={styles.inlineRetryButton}
                                        onClick={handleReloadCatalogs}
                                        disabled={isSubmitting}
                                    >
                                        Tải lại danh mục
                                    </button>
                                </>
                            ) : null}
                        </div>
                    </section>}
                </div>

                {error && (
                    <div className={styles.formError} role="alert">
                        <span className={styles.errorMark}>!</span>
                        <p>{error}</p>
                    </div>
                )}

                <SelectedMediaPreview file={selectedFile} />
                {selectedMediaType && <ProcessingPreferences
                    value={processingPreferences}
                    onChange={setProcessingPreferences}
                    disabled={isSubmitting}
                    mediaType={selectedMediaType}
                    variant="card"
                />}

                <footer className={styles.formFooter}>
                    <button
                        className={styles.secondaryButton}
                        type="button"
                        disabled={isSubmitting}
                        onClick={() => navigate('/library/list')}
                    >
                        Quay lại
                    </button>

                    <button
                        className={styles.primaryButton}
                        type="submit"
                        disabled={
                            isSubmitting ||
                            catalogLoading ||
                            metadataLoading ||
                            Boolean(catalogError) ||
                            Boolean(metadataError) ||
                            !resourceTypeCode ||
                            !selectedProvider
                        }
                    >
                        {isSubmitting && (
                            <span className={styles.buttonSpinner} />
                        )}
                        {submitStage === "creating"
                            ? "Đang tạo học liệu..."
                            : submitStage === "processing"
                                ? "Đang khởi chạy xử lý..."
                                : "Tạo và xử lý học liệu"}
                    </button>
                </footer>
            </form>
        </section>
    )
}

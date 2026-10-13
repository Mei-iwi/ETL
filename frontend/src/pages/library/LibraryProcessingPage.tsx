import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useLocation, useSearchParams } from 'react-router'
import {
    getContentUnits,
    getProcessingJob,
    isRetryableProcessingError,
    retryProcessing,
    startProcessing,
} from '../../services/processingApi'
import type {
    ContentUnitsResult,
    JobStatus,
    ProcessingStatus,
    ProcessResult,
} from '../../types/processing'
import styles from './LibraryPages.module.css'

type ProcessingView = {
    resource_version_id: string
    job_id: string
    job_status: JobStatus
    processing_status: ProcessingStatus
}

type ProcessingLocationState = {
    processingNotice?: string
}

const terminalJobStatuses: JobStatus[] = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED']
const statusRetryDelays = [4_000, 8_000, 16_000]
const contentPageSize = 10

function isAbortError(error: unknown) {
    return error instanceof DOMException && error.name === 'AbortError'
}

function getMainStatus(
    jobId: string,
    result: ProcessingView | null,
    statusError: string,
) {
    if (statusError) return { label: 'Không thể cập nhật trạng thái', tone: 'error' }
    if (!jobId) return { label: 'Chưa bắt đầu xử lý', tone: 'idle' }
    if (!result) return { label: 'Đang cập nhật trạng thái', tone: 'active' }

    const labels: Record<JobStatus, { label: string, tone: string }> = {
        PENDING: { label: 'Đang chờ xử lý', tone: 'active' },
        RUNNING: { label: 'Đang xử lý', tone: 'active' },
        COMPLETED: result.processing_status === 'CONTENT_READY'
            ? { label: 'Xử lý hoàn thành', tone: 'success' }
            : { label: 'Đã kết thúc xử lý', tone: 'idle' },
        COMPLETED_WITH_ERRORS: { label: 'Hoàn thành có lỗi', tone: 'warning' },
        FAILED: { label: 'Xử lý thất bại', tone: 'error' },
    }
    return labels[result.job_status]
}

export function LibraryProcessingPage() {
    const [searchParams, setSearchParams] = useSearchParams()
    const location = useLocation()
    const versionId = searchParams.get('versionId') ?? ''
    const jobId = searchParams.get('jobId') ?? ''
    const resourceId = searchParams.get('resourceId') ?? ''
    const backPath = resourceId ? `/library/${encodeURIComponent(resourceId)}` : '/library/list'
    const processingNotice = (location.state as ProcessingLocationState | null)?.processingNotice

    const [result, setResult] = useState<ProcessingView | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [statusError, setStatusError] = useState('')
    const [stoppedJobId, setStoppedJobId] = useState('')
    const [statusRefreshKey, setStatusRefreshKey] = useState(0)
    const [expandedContent, setExpandedContent] = useState<Record<string, boolean>>({})
    const [contentOffsets, setContentOffsets] = useState<Record<string, number>>({})
    const [loadedContent, setLoadedContent] = useState<{
        versionId: string
        offset: number
        data: ContentUnitsResult
    } | null>(null)
    const [contentLoading, setContentLoading] = useState(false)
    const [contentError, setContentError] = useState('')
    const actionControllerRef = useRef<AbortController | null>(null)
    const contentExpanded = expandedContent[versionId] ?? false
    const contentOffset = contentOffsets[versionId] ?? 0

    useEffect(() => () => actionControllerRef.current?.abort(), [])

    useEffect(() => {
        if (!jobId || loading) return

        let active = true
        let timerId: number | undefined
        let controller: AbortController | null = null
        let failedAttempts = 0

        async function loadStatus() {
            controller = new AbortController()
            try {
                const data = await getProcessingJob(jobId, controller.signal)
                if (!active) return

                failedAttempts = 0
                setStatusError('')
                setStoppedJobId('')
                setResult({
                    resource_version_id: data.resource_version_id,
                    job_id: data.id,
                    job_status: data.status,
                    processing_status: data.processing_status,
                })

                if (!terminalJobStatuses.includes(data.status)) {
                    timerId = window.setTimeout(() => void loadStatus(), 4_000)
                }
            } catch (caughtError) {
                if (!active || isAbortError(caughtError)) return
                const message = caughtError instanceof Error
                    ? caughtError.message
                    : 'Không thể tải trạng thái xử lý'
                setStatusError(message)
                if (isRetryableProcessingError(caughtError) && failedAttempts < statusRetryDelays.length) {
                    const delay = statusRetryDelays[failedAttempts]
                    failedAttempts += 1
                    timerId = window.setTimeout(() => void loadStatus(), delay)
                    return
                }

                setStoppedJobId(jobId)
            }
        }

        void loadStatus()
        return () => {
            active = false
            controller?.abort()
            if (timerId !== undefined) window.clearTimeout(timerId)
        }
    }, [jobId, loading, statusRefreshKey])

    const currentResult = result?.resource_version_id === versionId ? result : null
    const contentReady = currentResult?.processing_status === 'CONTENT_READY'
    const contentResult = loadedContent?.versionId === versionId && loadedContent.offset === contentOffset
        ? loadedContent.data
        : null

    useEffect(() => {
        if (!contentReady || !contentExpanded || !versionId) return

        const controller = new AbortController()
        async function loadContent() {
            setContentLoading(true)
            setContentError('')
            try {
                const data = await getContentUnits(versionId, contentOffset, contentPageSize, controller.signal)
                if (!controller.signal.aborted) {
                    setLoadedContent({ versionId, offset: contentOffset, data })
                }
            } catch (caughtError) {
                if (controller.signal.aborted || isAbortError(caughtError)) return
                setContentError(caughtError instanceof Error ? caughtError.message : 'Không thể tải nội dung đã trích xuất')
            } finally {
                if (!controller.signal.aborted) setContentLoading(false)
            }
        }

        void loadContent()
        return () => controller.abort()
    }, [contentExpanded, contentOffset, contentReady, versionId])

    function updateJobInUrl(data: ProcessResult) {
        const nextParams = new URLSearchParams(searchParams)
        nextParams.set('jobId', data.job_id)
        setSearchParams(nextParams, { replace: true })
    }

    async function handleStartProcessing() {
        if (!versionId) {
            setError('Không tìm thấy mã phiên bản học liệu')
            return
        }
        actionControllerRef.current?.abort()
        const controller = new AbortController()
        actionControllerRef.current = controller
        setLoading(true)
        setError('')

        try {
            const data = await startProcessing(versionId, controller.signal)
            if (actionControllerRef.current !== controller) return
            setResult(data)
            updateJobInUrl(data)
        } catch (caughtError) {
            if (isAbortError(caughtError)) return
            setError(caughtError instanceof Error ? caughtError.message : 'Không thể bắt đầu xử lý')
        } finally {
            if (actionControllerRef.current === controller) {
                actionControllerRef.current = null
                setLoading(false)
            }
        }
    }

    async function handleRetry() {
        if (!currentResult?.job_id) {
            setError('Không tìm thấy mã processing job')
            return
        }
        actionControllerRef.current?.abort()
        const controller = new AbortController()
        actionControllerRef.current = controller
        setLoading(true)
        setError('')
        try {
            const data = await retryProcessing(currentResult.job_id, controller.signal)
            if (actionControllerRef.current !== controller) return
            setResult(data)
            updateJobInUrl(data)
        } catch (caughtError) {
            if (isAbortError(caughtError)) return
            setError(caughtError instanceof Error ? caughtError.message : 'Không thể yêu cầu xử lý lại')
        } finally {
            if (actionControllerRef.current === controller) {
                actionControllerRef.current = null
                setLoading(false)
            }
        }
    }

    function handleCheckStatusAgain() {
        setStatusError('')
        setStoppedJobId('')
        setStatusRefreshKey((current) => current + 1)
    }

    const canRetry = currentResult?.processing_status === 'OCR_FAILED' || currentResult?.processing_status === 'FINALIZATION_FAILED'
    const mainStatus = getMainStatus(jobId, currentResult, statusError)

    if (!versionId) return <Navigate to="/library/list" replace />

    return (
        <section className={styles.page}>
            <Link className={styles.backLink} to={backPath}>
                {resourceId ? '← Quay lại chi tiết học liệu' : '← Quay lại danh sách học liệu'}
            </Link>
            <header className={styles.pageHeader}><h1>Xử lý dữ liệu</h1></header>

            <div className={styles.card}>
                <div className={styles.versionBox}>
                    <span>Mã phiên bản</span>
                    <span>{versionId || 'Chưa có'}</span>
                </div>
            </div>

            <div className={styles.resultCard}>
                <h2>Trạng thái xử lý</h2>
                <div className={styles.statusMessage} data-tone={mainStatus.tone} role="status">
                    <span aria-hidden="true">●</span>
                    <strong>{mainStatus.label}</strong>
                    {currentResult?.job_status === 'RUNNING' && (
                        <small>Hệ thống đang xử lý học liệu...</small>
                    )}
                </div>

                {processingNotice && <p className={styles.notice}>{processingNotice}</p>}
                {error && <p className={styles.alert} role="alert">{error}</p>}
                {statusError && <p className={styles.alert} role="alert">{statusError}</p>}

                {contentReady && (
                    <section className={styles.contentSection}>
                        <div className={styles.actions}>
                            <button
                                className={styles.primaryButton}
                                type="button"
                                onClick={() => setExpandedContent((current) => ({
                                    ...current,
                                    [versionId]: !contentExpanded,
                                }))}
                            >
                                {contentExpanded ? 'Thu gọn nội dung' : 'Xem nội dung trích xuất'}
                            </button>
                        </div>
                        {contentExpanded && contentLoading && <p role="status">Đang tải nội dung...</p>}
                        {contentExpanded && contentError && <p className={styles.alert} role="alert">{contentError}</p>}
                        {contentExpanded && !contentLoading && !contentError && contentResult?.items.length === 0 && <p>Chưa có nội dung để hiển thị.</p>}
                        {contentExpanded && contentResult && contentResult.items.length > 0 && (
                            <div className={styles.contentList}>
                                {contentResult.items.map((unit) => (
                                    <article className={styles.contentItem} key={unit.id}>
                                        <div className={styles.contentMeta}>
                                            <span>Đoạn {unit.sequence_no}</span>
                                            {unit.page_from !== null && (
                                                <span>Trang {unit.page_from}{unit.page_to !== null && unit.page_to !== unit.page_from ? `–${unit.page_to}` : ''}</span>
                                            )}
                                        </div>
                                        {unit.title && <h4>{unit.title}</h4>}
                                        <p>{unit.text}</p>
                                    </article>
                                ))}
                            </div>
                        )}
                        {contentExpanded && contentResult && contentResult.total > contentPageSize && (
                            <div className={styles.contentPagination}>
                                <button className={styles.primaryButton} type="button" disabled={contentLoading || contentOffset === 0} onClick={() => setContentOffsets((current) => ({ ...current, [versionId]: Math.max(0, contentOffset - contentPageSize) }))}>Trước</button>
                                <span>{contentResult.offset + 1}–{Math.min(contentResult.offset + contentResult.items.length, contentResult.total)} / {contentResult.total}</span>
                                <button className={styles.primaryButton} type="button" disabled={contentLoading || contentOffset + contentPageSize >= contentResult.total} onClick={() => setContentOffsets((current) => ({ ...current, [versionId]: contentOffset + contentPageSize }))}>Tiếp</button>
                            </div>
                        )}
                    </section>
                )}

                {currentResult && (
                    <div className={styles.resultGrid}>
                        <p className={styles.resultItem}>
                            <span className={styles.resultLabel}>Job ID</span>
                            <span className={styles.resultValue}>{currentResult.job_id}</span>
                        </p>
                        <p className={styles.resultItem}>
                            <span className={styles.resultLabel}>Trạng thái job</span>
                            <span className={styles.resultValue}>{currentResult.job_status}</span>
                        </p>
                        <p className={styles.resultItem}>
                            <span className={styles.resultLabel}>Trạng thái xử lý</span>
                            <span className={styles.resultValue}>{currentResult.processing_status}</span>
                        </p>
                    </div>
                )}

                {(!jobId || canRetry || stoppedJobId === jobId) && (
                    <div className={styles.actions}>
                        {!jobId && (
                            <button className={styles.primaryButton} type="button" onClick={() => void handleStartProcessing()} disabled={loading || !versionId}>
                                {loading ? 'Đang gửi yêu cầu...' : 'Bắt đầu xử lý'}
                            </button>
                        )}
                        {canRetry && (
                            <button className={styles.secondaryButton} type="button" onClick={() => void handleRetry()} disabled={loading}>
                                {loading ? 'Đang gửi yêu cầu...' : 'Xử lý lại'}
                            </button>
                        )}
                        {stoppedJobId === jobId && (
                            <button className={styles.primaryButton} type="button" onClick={handleCheckStatusAgain}>Kiểm tra lại trạng thái</button>
                        )}
                    </div>
                )}
            </div>
        </section>
    )
}

import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import {
    getProcessingJob,
    retryProcessing,
    startProcessing
} from '../../services/processingApi'
import type { JobStatus, ProcessingStatus, ProcessResult } from "../../types/processing"
import styles from './LibraryPages.module.css'

type ProcessingView = {
    resource_version_id: string
    job_id: string
    job_status: JobStatus
    processing_status: ProcessingStatus
    action?: string
}

const terminalStatuses: ProcessingStatus[] = [
    'OCR_FAILED',
    'FINALIZATION_FAILED',
    'CONTENT_READY',
]

function isAbortError(error: unknown) {
    return error instanceof DOMException && error.name === 'AbortError'
}

export function LibraryProcessingPage() {
    const [searchParams, setSearchParams] = useSearchParams()

    const versionId = searchParams.get('versionId') ?? ''
    const jobId = searchParams.get('jobId') ?? ''

    const [result, setResult] = useState<ProcessingView | null>(null)

    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const actionControllerRef = useRef<AbortController | null>(null)

    useEffect(() => {
        return () => actionControllerRef.current?.abort()
    }, [])

    useEffect(() => {
        if (!jobId || loading) return

        let active = true
        let timerId: number | undefined
        let controller: AbortController | null = null

        async function loadStatus() {
            controller = new AbortController()

            try {
                const data = await getProcessingJob(jobId, controller.signal)
                if (!active) return

                setResult((current) => ({
                    resource_version_id: data.resource_version_id,
                    job_id: data.id,
                    job_status: data.status,
                    processing_status: data.processing_status,
                    action: current?.job_id === data.id
                        ? current.action
                        : undefined,
                }))

                if (!terminalStatuses.includes(data.processing_status)) {
                    timerId = window.setTimeout(() => {
                        void loadStatus()
                    }, 4_000)
                }
            } catch (e) {
                if (!active || isAbortError(e)) return
                setError(
                    e instanceof Error
                        ? e.message
                        : 'Không thể tải trạng thái xử lý',
                )
            }
        }

        void loadStatus()

        return () => {
            active = false
            controller?.abort()
            if (timerId !== undefined) window.clearTimeout(timerId)
        }
    }, [jobId, loading])

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
        } catch (e) {
            if (isAbortError(e)) return
            setError(
                e instanceof Error ? e.message : 'Không thể bắt đầu xử lý',
            )
        }
        finally {
            if (actionControllerRef.current === controller) {
                actionControllerRef.current = null
                setLoading(false)
            }
        }
    }

    async function handleRetry() {
        if (!result?.job_id) {
            setError('Không tìm thấy mã processing job')
            return
        }
        actionControllerRef.current?.abort()
        const controller = new AbortController()
        actionControllerRef.current = controller
        setLoading(true)
        setError('')
        try {
            const data = await retryProcessing(result.job_id, controller.signal)
            if (actionControllerRef.current !== controller) return
            setResult(data)
            updateJobInUrl(data)
        }
        catch (e) {
            if (isAbortError(e)) return
            setError(
                e instanceof Error ? e.message : 'Không thể yêu cầu xử lý lại'
            )
        } finally {
            if (actionControllerRef.current === controller) {
                actionControllerRef.current = null
                setLoading(false)
            }
        }
    }

    const canRetry = result?.processing_status === 'OCR_FAILED' ||
        result?.processing_status === 'FINALIZATION_FAILED'

    return (
        <section className={styles.page}>
            <header className={styles.pageHeader}>
                <h1>Xử lý dữ liệu</h1>
            </header>

            <div className={styles.card}>
                <div className={styles.versionBox}>
                    <span>Mã phiên bản</span>
                    <span>{versionId || 'Chưa có'}</span>
                </div>
                <div className={styles.actions}>
                    <button className={styles.primaryButton} type="button" onClick={() => void handleStartProcessing()} disabled={loading || !versionId}>
                        {loading ? 'Đang gửi yêu cầu...' : 'Bắt đầu xử lý'}
                    </button>
                </div>
            </div>
            {error && (
                <p className={styles.alert} role="alert">{error}</p>
            )}

            {result && (
                <div className={styles.resultCard}>
                    <h2>Kết quả yêu cầu</h2>
                    <div className={styles.resultGrid}>
                        <p className={styles.resultItem}>
                            <span className={styles.resultLabel}>Job ID</span>
                            <span className={styles.resultValue}>{result.job_id}</span>
                        </p>
                        <p className={styles.resultItem}>
                            <span className={styles.resultLabel}>Trạng thái xử lý</span>
                            <span className={styles.resultValue}>{result.processing_status}</span>
                        </p>
                        {result.action && (
                            <p className={styles.resultItem}>
                                <span className={styles.resultLabel}>Hành động</span>
                                <span className={styles.resultValue}>{result.action}</span>
                            </p>
                        )}
                    </div>
                    {canRetry && (
                        <div className={styles.actions}>
                            <button className={styles.secondaryButton} type="button" onClick={() => void handleRetry()} disabled={loading}>
                                {loading ? 'Đang gửi yêu cầu...' : 'Xử lý lại'}
                            </button>
                        </div>
                    )}
                </div>
            )}
        </section>
    )
}

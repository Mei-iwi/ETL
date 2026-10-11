import { useState } from "react"
import { useSearchParams } from "react-router"
import {
    retryProcessing,
    startProcessing
} from '../../services/processingApi'
import type { ProcessResult } from "../../types/processing"
import styles from './LibraryPages.module.css'

export function LibraryProcessingPage() {
    const [searchParams] = useSearchParams()

    const versionId = searchParams.get('versionId') ?? ''

    const [result, setResult] = useState<ProcessResult | null>(null)

    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    async function handleStartProcessing() {
        if (!versionId) {
            setError('Không tìm thấy mã phiên bản học liệu')
            return
        }
        setLoading(true)
        setError('')

        try {
            const data = await startProcessing(versionId)
            setResult(data)
        } catch (e) {
            setError(
                e instanceof Error ? e.message : 'Không thể bắt đầu xử lý',
            )
        }
        finally {
            setLoading(false)
        }
    }

    async function handleRetry() {
        if (!result?.job_id) {
            setError('Không tìm thấy mã processing job')
            return
        }
        setLoading(true)
        setError('')
        try {
            const data = await retryProcessing(result.job_id)
            setResult(data)
        }
        catch (e) {
            setError(
                e instanceof Error ? e.message : 'Không thể yêu cầu xử lý lại'
            )
        } finally {
            setLoading(false)
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
                        <p className={styles.resultItem}>
                            <span className={styles.resultLabel}>Hành động</span>
                            <span className={styles.resultValue}>{result.action}</span>
                        </p>
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

import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import {
    configureMasterSyncAuto,
    getMasterSyncStatus,
    syncMasterData,
} from '../../services/masterSyncApi'
import type {
    MasterSyncResponse,
    MasterSyncStatusResponse,
    MasterSyncStream,
} from '../../types/masterSync'
import styles from './SystemMasterSyncPage.module.css'

const streamLabels: Record<string, string> = {
    resources: 'Học liệu',
    users: 'Người dùng',
    grades: 'Khối lớp',
    subjects: 'Môn học',
}

const statusLabels = {
    RUNNING: 'Đang chạy',
    COMPLETED: 'Thành công',
    FAILED: 'Thất bại',
} as const

const autoUnavailableLabels: Record<string, string> = {
    UNSUPPORTED_ENVIRONMENT: 'Cần PostgreSQL và worker',
    SOURCE_UNAVAILABLE: 'Không kết nối được dữ liệu nguồn',
    SYNC_FAILED: 'Lần tự động gần nhất thất bại',
    SYNC_BUSY: 'Đang có lượt đồng bộ khác',
}

export function SystemMasterSyncPage() {
    const [stream, setStream] = useState<MasterSyncStream>('all')
    const [full, setFull] = useState(false)
    const [loading, setLoading] = useState(false)
    const [actionError, setActionError] = useState('')
    const [statusError, setStatusError] = useState('')
    const [statusLoading, setStatusLoading] = useState(true)
    const [autoPending, setAutoPending] = useState(false)
    const [result, setResult] = useState<MasterSyncResponse | null>(null)
    const [syncStatus, setSyncStatus] = useState<MasterSyncStatusResponse | null>(null)
    const submittingRef = useRef(false)

    useEffect(() => {
        let active = true
        let controller: AbortController | null = null
        let timer: number | undefined

        async function pollStatus() {
            controller = new AbortController()
            try {
                const response = await getMasterSyncStatus(controller.signal)
                if (active) {
                    setSyncStatus(response)
                    setStatusError('')
                }
            } catch (caughtError) {
                if (active && !controller.signal.aborted) {
                    setStatusError(
                        caughtError instanceof Error
                            ? caughtError.message
                            : 'Không thể tải trạng thái đồng bộ',
                    )
                }
            } finally {
                if (active) {
                    setStatusLoading(false)
                    timer = window.setTimeout(pollStatus, 5000)
                }
            }
        }

        void pollStatus()
        return () => {
            active = false
            if (timer !== undefined) window.clearTimeout(timer)
            controller?.abort()
        }
    }, [])

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault()
        if (submittingRef.current) return
        if (full && !window.confirm('Chạy Full Sync cho phạm vi đã chọn?')) return

        submittingRef.current = true
        setLoading(true)
        setActionError('')
        try {
            const response = await syncMasterData({ stream, full })
            setResult(response)
        } catch (caughtError) {
            setActionError(caughtError instanceof Error ? caughtError.message : 'Không thể đồng bộ dữ liệu')
        } finally {
            submittingRef.current = false
            setLoading(false)
            getMasterSyncStatus().then(setSyncStatus).catch(() => undefined)
        }
    }

    async function handleAutoChange(enabled: boolean, autoStream: MasterSyncStream = 'all') {
        setAutoPending(true)
        setActionError('')
        try {
            setSyncStatus(await configureMasterSyncAuto(enabled, autoStream))
        } catch (caughtError) {
            setActionError(caughtError instanceof Error ? caughtError.message : 'Không thể cập nhật Auto Sync')
        } finally {
            setAutoPending(false)
        }
    }

    const currentRun = syncStatus?.runs.find((run) => run.status === 'RUNNING')
    const latestRun = syncStatus?.runs.find((run) => run.status !== 'RUNNING')
    const latestResults = syncStatus?.runs.reduce<MasterSyncResponse>((runs, run) => {
        if (!(run.stream_name in runs)) runs[run.stream_name] = run
        return runs
    }, {})
    const displayedResults = latestResults && Object.keys(latestResults).length > 0
        ? latestResults
        : result
    const syncInProgress = loading || Boolean(currentRun) || Boolean(syncStatus?.auto_running)

    let currentStatus = 'Không có job đang chạy'
    if (statusLoading && !syncStatus) currentStatus = 'Đang tải...'
    else if (statusError) currentStatus = 'Không thể cập nhật'
    else if (syncInProgress) currentStatus = 'Đang đồng bộ'

    function formatTime(value: string | null | undefined) {
        if (!value) return 'Chưa có'
        return new Intl.DateTimeFormat('vi-VN', {
            dateStyle: 'short',
            timeStyle: 'medium',
        }).format(new Date(value))
    }

    return (
        <section className={styles.page}>
            <h1>Đồng bộ dữ liệu nguồn</h1>
            <div className={`${styles.card} ${styles.statusCard}`}>
                <div>
                    <span className={styles.statusLabel}>Trạng thái hiện tại</span>
                    <strong className={styles.statusValue}>
                        {currentStatus}
                    </strong>
                </div>
                <div>
                    <span className={styles.statusLabel}>Lần đồng bộ gần nhất</span>
                    <strong className={styles.statusValue}>{formatTime(latestRun?.finished_at ?? latestRun?.started_at)}</strong>
                </div>
                <div>
                    <span className={styles.statusLabel}>Kết quả</span>
                    <strong className={styles.statusValue}>
                        {latestRun ? statusLabels[latestRun.status] : 'Chưa có dữ liệu'}
                    </strong>
                </div>
                <div>
                    <span className={styles.statusLabel}>Chế độ</span>
                    <strong className={styles.statusValue}>{latestRun?.sync_mode ?? 'Chưa xác định'}</strong>
                </div>
            </div>
            <form className={`${styles.card} ${styles.form}`} onSubmit={handleSubmit}>
                <div className={styles.field}>
                    <label htmlFor="syncStream">Nhóm dữ liệu</label>
                    <select
                        id="syncStream"
                        value={stream}
                        onChange={(event) => setStream(event.target.value as MasterSyncStream)}
                        disabled={syncInProgress}
                    >
                        <option value="all">Tất cả</option>
                        <option value="resources">Học liệu</option>
                        <option value="users">Người dùng</option>
                        <option value="grades">Khối lớp</option>
                        <option value="subjects">Môn học</option>
                    </select>
                </div>
                <div className={styles.field}>
                    <label htmlFor="syncMode">Chế độ đồng bộ</label>
                    <select
                        id="syncMode"
                        value={full ? 'full' : 'incremental'}
                        onChange={(event) => setFull(event.target.value === 'full')}
                        disabled={syncInProgress}
                    >
                        <option value="incremental">Incremental</option>
                        <option value="full">Full</option>
                    </select>
                </div>
                {syncStatus && (
                    <div className={styles.autoPanel}>
                        <div className={styles.autoHeader}>
                            <strong>Tự động đồng bộ</strong>
                            <span>
                                {syncStatus.auto_supported
                                    ? 'Khả dụng'
                                    : autoUnavailableLabels[syncStatus.auto_error ?? ''] ?? 'Không khả dụng'}
                            </span>
                        </div>
                        <label className={styles.autoRow}>
                            <span>Bật/tắt tất cả</span>
                            <span>Incremental</span>
                            <input
                                type="checkbox"
                                checked={syncStatus.auto_all_enabled}
                                disabled={!syncStatus.auto_supported || autoPending}
                                onChange={(event) => void handleAutoChange(event.target.checked)}
                            />
                        </label>
                        {syncStatus.auto_streams.map((autoStream) => (
                            <label className={styles.autoRow} key={autoStream.stream_name}>
                                <span>{streamLabels[autoStream.stream_name]}</span>
                                <span>Incremental</span>
                                <input
                                    type="checkbox"
                                    checked={autoStream.enabled}
                                    disabled={!syncStatus.auto_supported || autoPending}
                                    onChange={(event) => void handleAutoChange(
                                        event.target.checked,
                                        autoStream.stream_name,
                                    )}
                                />
                            </label>
                        ))}
                    </div>
                )}
                <div className={styles.actions}>
                    <button className={styles.button} type="submit" disabled={syncInProgress}>
                        {loading
                            ? 'Đang đồng bộ...'
                            : stream === 'all'
                                ? 'Đồng bộ toàn bộ'
                                : `Đồng bộ ${streamLabels[stream]}`}
                    </button>
                </div>
            </form>

            {(actionError || statusError) && (
                <p className={styles.error} role="alert">{actionError || statusError}</p>
            )}

            {displayedResults && Object.keys(displayedResults).length > 0 && (
                <div className={styles.results}>
                    {Object.entries(displayedResults).map(([name, data]) => (
                        <article className={styles.resultCard} key={name}>
                            <h2>{streamLabels[name] ?? name}</h2>
                            <dl className={styles.metrics}>
                                <div><dt>Đã đọc</dt><dd>{data.records_read}</dd></div>
                                <div><dt>Đã thêm</dt><dd>{data.inserted}</dd></div>
                                <div><dt>Đã cập nhật</dt><dd>{data.updated}</dd></div>
                                <div><dt>Bỏ qua</dt><dd>{data.skipped}</dd></div>
                                <div><dt>Lỗi</dt><dd>{data.failed}</dd></div>
                            </dl>
                        </article>
                    ))}
                </div>
            )}
        </section>
    )
}

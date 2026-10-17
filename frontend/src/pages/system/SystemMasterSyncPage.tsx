import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Link, NavLink, Outlet } from 'react-router'
import {
    configureMasterSyncAuto,
    getMasterSyncStatus,
    getSourceSyncFiles,
    retrySourceFile,
    retryFailedSourceFiles,
    syncMasterData,
} from '../../services/masterSyncApi'
import type {
    MasterSyncStatusResponse,
    MasterSyncStream,
    MasterSyncMode,
    SourceSyncFilesResponse,
} from '../../types/masterSync'
import styles from './SystemMasterSyncPage.module.css'
import { acceptStatusPoll, configuredModeLabel, selectSyncResults } from './masterSyncResults'
import type { ManualSyncResult } from './masterSyncResults'

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

export function SystemMasterSyncLayout() {
    return <section className={styles.page}>
        <h1>Đồng bộ dữ liệu nguồn</h1>
        <nav className={styles.errorTabs} aria-label="Nhóm đồng bộ dữ liệu">
            <NavLink to="/library/source-sync" end>Cấu hình đồng bộ</NavLink>
            <NavLink to="/library/source-sync/files" end>Tệp học liệu từ nguồn</NavLink>
        </nav>
        <Outlet />
    </section>
}

export function SystemMasterSyncPage() {
    const { confirm, dialog } = useConfirmation()
    const [loading, setLoading] = useState(false)
    const [actionError, setActionError] = useState('')
    const [statusError, setStatusError] = useState('')
    const [statusLoading, setStatusLoading] = useState(true)
    const [autoPending, setAutoPending] = useState(false)
    const [result, setResult] = useState<ManualSyncResult | null>(null)
    const [runningMode, setRunningMode] = useState<MasterSyncMode | null>(null)
    const [syncStatus, setSyncStatus] = useState<MasterSyncStatusResponse | null>(null)
    const submittingRef = useRef(false)
    const statusRevisionRef = useRef(0)
    const [statusRefresh, setStatusRefresh] = useState(0)

    useEffect(() => {
        let active = true
        let controller: AbortController | null = null
        let timer: number | undefined

        async function pollStatus() {
            if (timer !== undefined) window.clearTimeout(timer)
            controller?.abort()
            const requestController = new AbortController()
            controller = requestController
            const revision = statusRevisionRef.current
            try {
                const response = await getMasterSyncStatus(requestController.signal)
                if (acceptStatusPoll(active, requestController.signal.aborted, revision, statusRevisionRef.current, submittingRef.current)) {
                    setSyncStatus(response)
                    setResult((manual) => manual && Object.entries(manual.response).every(([stream, data]) =>
                        response.runs.some((run) => run.stream_name === stream && run.run_id === data.run_id)) ? null : manual)
                    setStatusError('')
                }
            } catch (caughtError) {
                if (acceptStatusPoll(active, requestController.signal.aborted, revision, statusRevisionRef.current, submittingRef.current)) {
                    setStatusError(
                        caughtError instanceof Error
                            ? caughtError.message
                            : 'Không thể tải trạng thái đồng bộ',
                    )
                }
            } finally {
                if (active && controller === requestController) {
                    setStatusLoading(false)
                    timer = window.setTimeout(pollStatus, document.hidden ? 15000 : 2000)
                }
            }
        }

        function refreshWhenVisible() {
            if (!document.hidden) void pollStatus()
        }
        document.addEventListener('visibilitychange', refreshWhenVisible)
        window.addEventListener('focus', refreshWhenVisible)
        void pollStatus()
        return () => {
            active = false
            if (timer !== undefined) window.clearTimeout(timer)
            controller?.abort()
            document.removeEventListener('visibilitychange', refreshWhenVisible)
            window.removeEventListener('focus', refreshWhenVisible)
        }
    }, [statusRefresh])

    async function handleManualSync(mode: MasterSyncMode) {
        if (submittingRef.current) return
        const message = mode === 'FULL' ? 'Chạy Full Sync cho tất cả nhóm dữ liệu?'
            : 'Chạy Incremental cho tất cả nhóm? Nếu chưa có checkpoint, lần này có thể đọc toàn bộ dữ liệu.'
        if (!await confirm(message) || submittingRef.current) return

        submittingRef.current = true
        statusRevisionRef.current += 1
        setLoading(true)
        setRunningMode(mode)
        setActionError('')
        setResult(null)
        try {
            const response = await syncMasterData({ stream: 'all', full: mode === 'FULL' })
            setResult({ response, mode })
        } catch (caughtError) {
            setActionError(caughtError instanceof Error ? caughtError.message : 'Không thể đồng bộ dữ liệu')
        } finally {
            statusRevisionRef.current += 1
            submittingRef.current = false
            setLoading(false)
            setRunningMode(null)
            setStatusRefresh((value) => value + 1)
        }
    }

    async function handleAutoChange(enabled: boolean | undefined, autoStream: MasterSyncStream = 'all', mode?: MasterSyncMode) {
        if (submittingRef.current) return
        if (mode) {
            const group = autoStream === 'all' ? 'tất cả nhóm' : streamLabels[autoStream]
            const message = `Áp dụng ${mode === 'FULL' ? 'Full' : 'Incremental'} cho ${group}?`
                + (mode === 'FULL' ? ' Nhóm được bật sẽ chạy Full theo lịch.' : '')
            if (!await confirm(message)) return
        }
        submittingRef.current = true
        statusRevisionRef.current += 1
        setAutoPending(true)
        setActionError('')
        try {
            setSyncStatus(await configureMasterSyncAuto(enabled, autoStream, mode))
        } catch (caughtError) {
            setActionError(caughtError instanceof Error ? caughtError.message : 'Không thể cập nhật Auto Sync')
        } finally {
            statusRevisionRef.current += 1
            submittingRef.current = false
            setAutoPending(false)
            setStatusRefresh((value) => value + 1)
        }
    }

    const currentRun = syncStatus?.runs.find((run) => run.status === 'RUNNING')
    const latestRun = syncStatus?.runs.find((run) => run.status !== 'RUNNING')
    const displayedResults = selectSyncResults(syncStatus?.runs ?? [], result)
    const pendingStatus = Object.values(displayedResults).some((run) => run.status === 'PENDING_STATUS')
    const enabledStreams = syncStatus?.auto_streams.filter((item) => item.enabled) ?? []
    const syncInProgress = loading || Boolean(currentRun) || Boolean(syncStatus?.auto_running)
    const modes = new Set(syncStatus?.auto_streams.map((item) => item.sync_mode))
    const allMode = modes.size === 1 ? syncStatus?.auto_streams[0]?.sync_mode : ''
    const configuredModes = new Set(
        (enabledStreams.length > 0 ? enabledStreams : syncStatus?.auto_streams ?? [])
            .map((item) => item.sync_mode),
    )
    const displayedMode = configuredModeLabel([...configuredModes])
    const visibleStreams = [...new Set([...enabledStreams.map((item) => item.stream_name), ...Object.keys(result?.response ?? {})])]
    if (visibleStreams.length === 0) visibleStreams.push(...Object.keys(displayedResults))

    let currentStatus = 'Không có job đang chạy'
    if (statusLoading && !syncStatus) currentStatus = 'Đang tải...'
    else if (statusError) currentStatus = 'Không thể cập nhật'
    else if (syncInProgress) currentStatus = `Đang đồng bộ (${runningMode ?? currentRun?.sync_mode ?? 'Auto'})`

    const fileNotice = (syncStatus?.file_sync_supported === false
        ? syncStatus.file_sync_error === 'BLOCKED_SOURCE_DATABASE'
            ? 'Chưa có nguồn PostgreSQL.'
            : 'Kho tệp nguồn chưa sẵn sàng.'
        : syncStatus?.file_sync_supported && !syncStatus.file_worker_healthy
            ? 'Worker nhập tệp chưa hoạt động.'
            : '')

    function formatTime(value: string | null | undefined) {
        if (!value) return 'Chưa có'
        return new Intl.DateTimeFormat('vi-VN', {
            dateStyle: 'short',
            timeStyle: 'medium',
        }).format(new Date(value))
    }

    return (
        <div>
            {fileNotice && <p className={styles.notice} role="status">{fileNotice}</p>}
            {(actionError || statusError) && <p className={styles.error} role="alert">{actionError || statusError}</p>}
            <div className={`${styles.card} ${styles.statusCard}`}>
                <div>
                    <span className={styles.statusLabel}>Trạng thái hiện tại</span>
                    <strong className={styles.statusValue}>
                        {currentStatus}
                    </strong>
                </div>
                <div>
                    <span className={styles.statusLabel}>Lần đồng bộ gần nhất</span>
                    <strong className={styles.statusValue}>{pendingStatus ? 'Đang tải lần vừa chạy' : formatTime(latestRun?.finished_at ?? latestRun?.started_at)}</strong>
                </div>
                <div>
                    <span className={styles.statusLabel}>Kết quả PostgreSQL Master</span>
                    <strong className={styles.statusValue}>
                        {pendingStatus ? 'Đang cập nhật trạng thái' : latestRun ? `${latestRun.sync_mode ?? 'Chưa rõ chế độ'}: ${statusLabels[latestRun.status]}` : 'Chưa có dữ liệu'}
                    </strong>
                </div>
                <div>
                    <span className={styles.statusLabel}>Chế độ tự động đã cấu hình</span>
                    <strong className={styles.statusValue}>{displayedMode}</strong>
                </div>
            </div>
            <div className={styles.card}>
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
                        <div className={styles.autoControls}>
                            <div className={styles.autoRow}>
                                <label htmlFor="sync-all-enabled">Bật/tắt tất cả</label>
                                <select className={styles.modeSelect} aria-label="Chế độ đồng bộ tất cả"
                                    value={allMode ?? ''} disabled={autoPending || syncInProgress}
                                    onChange={(event) => void handleAutoChange(undefined, 'all', event.target.value as MasterSyncMode)}>
                                    <option value="" disabled>Theo từng nhóm</option>
                                    <option value="INCREMENTAL">Incremental</option>
                                    <option value="FULL">Full</option>
                                </select>
                                <input
                                    id="sync-all-enabled"
                                    type="checkbox"
                                    checked={syncStatus.auto_all_enabled}
                                    disabled={(!syncStatus.auto_supported && !syncStatus.auto_all_enabled) || autoPending || loading}
                                    onChange={(event) => void handleAutoChange(event.target.checked)}
                                />
                            </div>
                            {syncStatus.auto_streams.map((autoStream) => (
                                <div className={styles.autoRow} key={autoStream.stream_name}>
                                    <label htmlFor={`sync-${autoStream.stream_name}-enabled`}>{streamLabels[autoStream.stream_name]}</label>
                                    <select className={styles.modeSelect} aria-label={`Chế độ đồng bộ ${streamLabels[autoStream.stream_name]}`}
                                        value={autoStream.sync_mode} disabled={autoPending || syncInProgress}
                                        onChange={(event) => void handleAutoChange(undefined, autoStream.stream_name, event.target.value as MasterSyncMode)}>
                                        <option value="INCREMENTAL">Incremental</option>
                                        <option value="FULL">Full</option>
                                    </select>
                                    <input
                                        id={`sync-${autoStream.stream_name}-enabled`}
                                        type="checkbox"
                                        checked={autoStream.enabled}
                                        disabled={(!syncStatus.auto_supported && !autoStream.enabled) || autoPending || loading}
                                        onChange={(event) => void handleAutoChange(
                                            event.target.checked,
                                            autoStream.stream_name,
                                        )}
                                    />
                                </div>
                            ))}
                        </div>
                    </div>
                )}
                <details className={styles.manualSync}>
                    <summary>Khởi tạo / đối soát</summary>
                    <button className={styles.button} type="button" disabled={syncInProgress || autoPending || !syncStatus}
                        onClick={() => void handleManualSync('FULL')}>{loading && runningMode === 'FULL' ? 'Đang chạy Full...' : 'Đồng bộ Full'}</button>
                    <button className={styles.button} type="button" disabled={syncInProgress || autoPending || !syncStatus}
                        onClick={() => void handleManualSync('INCREMENTAL')}>{loading && runningMode === 'INCREMENTAL' ? 'Đang chạy Incremental...' : 'Chạy Incremental'}</button>
                </details>
            </div>

            {syncStatus && (
                <div className={`${styles.card} ${styles.results}`}>
                    {enabledStreams.length === 0 && <p className={styles.notice}>Tự động đang tắt; bảng dưới là lịch sử hoặc lần chạy thủ công.</p>}
                    {visibleStreams.length === 0 ? <p className={styles.notice}>Chưa có lần đồng bộ.</p> : (
                        <div className={styles.tableWrap}>
                            <table className={styles.fileTable}>
                                <thead><tr><th>Nhóm dữ liệu</th><th>Lần chạy</th><th>Thời điểm</th><th title="Số ứng viên đã kiểm tra, gồm replay; không phải số bản ghi đã thay đổi">Đã đọc</th><th>Đã thêm</th><th>Cập nhật</th><th>Bỏ qua</th><th>Lỗi</th></tr></thead>
                                <tbody>
                                    {visibleStreams.map((name) => {
                                        const data = displayedResults?.[name]
                                        return (
                                            <tr key={name} title={data ? `Run ID: ${data.run_id}` : 'Chưa chạy'}>
                                                <td>{streamLabels[name] ?? name}</td>
                                                <td>{data ? `${data.sync_mode === 'FULL' ? 'Full' : data.sync_mode === 'INCREMENTAL' ? 'Incremental' : 'Chưa rõ chế độ'} · ${data.status === 'PENDING_STATUS' ? 'Đang tải trạng thái' : statusLabels[data.status]}` : 'Chưa chạy'}</td>
                                                <td>{data?.status === 'PENDING_STATUS' ? 'Đang tải...' : formatTime(data?.finished_at ?? data?.started_at)}</td>
                                                <td>{data?.records_read ?? '—'}</td><td>{data?.inserted ?? '—'}</td><td>{data?.updated ?? '—'}</td>
                                                <td>{data?.skipped ?? '—'}</td><td>{data?.failed ?? '—'}</td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}
            {dialog}
        </div>
    )
}

export function SourceSyncFilesSection({ onError, failedOnly = false, retryableOnly }: {
    onError: (message: string) => void
    failedOnly?: boolean
    retryableOnly?: boolean
}) {
    const { confirm, dialog } = useConfirmation()
    const [cursors, setCursors] = useState<Array<string | null>>([null])
    const [files, setFiles] = useState<SourceSyncFilesResponse | null>(null)
    const [loading, setLoading] = useState(true)
    const [retryPending, setRetryPending] = useState(false)
    const [retryMessage, setRetryMessage] = useState('')
    const [refresh, setRefresh] = useState(0)
    const retryingRef = useRef(false)
    const filesRevisionRef = useRef(0)
    const cursor = cursors[cursors.length - 1]

    useEffect(() => {
        let active = true
        let timer: number | undefined
        let controller: AbortController | undefined
        async function pollFiles() {
            if (timer !== undefined) window.clearTimeout(timer)
            controller?.abort()
            const requestController = new AbortController()
            controller = requestController
            const revision = filesRevisionRef.current
            try {
                const response = await getSourceSyncFiles(cursor, requestController.signal, failedOnly ? {
                    size: 7,
                    status: retryableOnly === undefined ? 'failed' : retryableOnly ? 'failed-retryable' : 'failed-blocked',
                } : { size: 7 })
                if (active && !requestController.signal.aborted && revision === filesRevisionRef.current && !retryingRef.current) {
                    setFiles(response)
                    onError('')
                }
            } catch (caughtError) {
                if (active && !requestController.signal.aborted && revision === filesRevisionRef.current && !retryingRef.current) {
                    onError(caughtError instanceof Error ? caughtError.message : 'Không thể tải tệp nguồn')
                }
            } finally {
                if (active && controller === requestController) {
                    setLoading(false)
                    timer = window.setTimeout(pollFiles, document.hidden ? 15000 : 2000)
                }
            }
        }
        function refreshWhenVisible() {
            if (!document.hidden) void pollFiles()
        }
        document.addEventListener('visibilitychange', refreshWhenVisible)
        window.addEventListener('focus', refreshWhenVisible)
        void pollFiles()
        return () => {
            active = false
            controller?.abort()
            if (timer !== undefined) window.clearTimeout(timer)
            document.removeEventListener('visibilitychange', refreshWhenVisible)
            window.removeEventListener('focus', refreshWhenVisible)
        }
    }, [cursor, refresh, onError, failedOnly, retryableOnly])

    async function retry(fileId?: string) {
        if (retryingRef.current) return
        if (!fileId && !await confirm('Thử lại các tệp lỗi đủ điều kiện?')) return
        retryingRef.current = true
        filesRevisionRef.current += 1
        setRetryPending(true)
        setRetryMessage('')
        try {
            if (fileId) {
                await retrySourceFile(fileId)
                setRetryMessage('Đã xếp hàng thử lại.')
            } else {
                let after: string | null = null
                let queued = 0
                let conflicts = 0
                // Each request enqueues at most 100 candidates; bound a user action as well.
                for (let batch = 0; batch < 10; batch += 1) {
                    const response = await retryFailedSourceFiles(after)
                    queued += response.accepted_count
                    conflicts += response.conflict_count
                    after = response.next_cursor
                    if (!after) break
                }
                setRetryMessage(`Đã xếp hàng ${queued} tệp.${conflicts ? ` ${conflicts} tệp chưa thể thử lại.` : ''}${after ? ' Còn tệp, hãy kiểm tra và thử lại tiếp.' : ''}`)
            }
        } catch (caughtError) {
            setRetryMessage(caughtError instanceof Error ? caughtError.message : 'Không thể thử lại')
        } finally {
            filesRevisionRef.current += 1
            retryingRef.current = false
            setRetryPending(false)
            if (failedOnly) setCursors([null])
            setRefresh((value) => value + 1)
        }
    }

    const importLabels: Record<string, string> = {
        UNTRACKED: 'Chưa có ánh xạ', PENDING: 'Đang chờ', FETCHING: 'Đang nhập',
        IMPORTED: 'Đã nhập', PROCESSING: 'Đã nhập', RETRY_WAIT: 'Chờ thử lại',
        RETRY_PROCESSING: 'Chờ thử lại ETL', FAILED: 'Lỗi', RETRY_EXHAUSTED: 'Hết lượt tự thử',
    }
    const etlLabels: Record<string, string> = {
        OCR_PENDING: 'Chờ OCR', OCR_RUNNING: 'Đang OCR', OCR_FAILED: 'Lỗi OCR',
        FINALIZATION_PENDING: 'Chờ chuẩn hóa', FINALIZING: 'Đang hoàn tất',
        FINALIZATION_FAILED: 'Lỗi hoàn tất', CONTENT_READY: 'Hoàn tất',
    }
    const errorLabels: Record<string, string> = {
        FILE_NOT_FOUND: 'Không tìm thấy tệp', PERMISSION_DENIED: 'Thiếu quyền đọc',
        INVALID_SOURCE_PATH: 'Tham chiếu tệp không hợp lệ', SOURCE_HASH_MISMATCH: 'Nội dung không khớp',
        INVALID_SOURCE_HASH: 'Hash nguồn không hợp lệ', INVALID_PDF: 'PDF không hợp lệ',
        FILE_TOO_LARGE: 'Tệp quá lớn', BLOCKED_SOURCE_STORAGE: 'Chưa cấu hình kho tệp nguồn',
        BLOCKED_SOURCE_POLICY: 'Trạng thái nguồn chưa được phép nhập',
        BLOCKED_SOURCE_MAPPING: 'Xung đột phiên bản', SOURCE_VERSION_MUTATED: 'Phiên bản nguồn bị thay đổi',
        WAITING_FOR_MASTER: 'Học liệu Master chưa sẵn sàng', SOURCE_STORAGE_UNAVAILABLE: 'Kho nguồn gián đoạn',
        UNSUPPORTED_SOURCE_FORMAT: 'Định dạng nguồn chưa hỗ trợ xử lý',
        INVALID_SOURCE_FILE: 'Tệp nguồn không hợp lệ', UNSUPPORTED_TEXT_ENCODING: 'Tệp cần dùng UTF-8',
        INVALID_IMAGE: 'Ảnh không hợp lệ hoặc vượt giới hạn pixel', BLOCKED_OCR_RUNTIME: 'Cần cấu hình Tesseract',
        OPENXML_TYPE_MISMATCH: 'Định dạng OpenXML không khớp',
        INVALID_OPENXML_CONTAINER: 'Tệp OpenXML không hợp lệ', INVALID_OPENXML_XML: 'XML không hợp lệ',
        OPENXML_LIMIT_EXCEEDED: 'OpenXML vượt giới hạn an toàn', UNSUPPORTED_OPENXML_MACROS: 'Không hỗ trợ macro',
        STT_RUNTIME_UNAVAILABLE: 'Chưa có runtime/model STT', MEDIA_NO_AUDIO: 'Tệp không có luồng âm thanh',
        MEDIA_DURATION_EXCEEDED: 'Thời lượng vượt giới hạn', INVALID_MEDIA: 'Audio/video không hợp lệ',
    }

    function changePage(next: Array<string | null>) {
        setCursors(next)
        setFiles(null)
        onError('')
        setLoading(true)
    }

    return (
        <section className={`${styles.card} ${styles.files}`} aria-label={failedOnly ? 'Tệp đồng bộ lỗi' : 'Tệp học liệu từ nguồn'}>
            {dialog}
            <div className={styles.filesHeader}>
                <h2>{failedOnly ? 'Tệp đồng bộ lỗi' : 'Tệp học liệu từ nguồn'}</h2>
                {files?.items.some((file) => file.retry_eligible) && <button className={`${styles.button} ${styles.fileAction}`} type="button"
                    disabled={retryPending} onClick={() => void retry()}>Thử lại tất cả file lỗi</button>}
            </div>
            {files && !files.file_sync_supported && <p className={styles.notice}>Kho tệp nguồn chưa sẵn sàng.</p>}
            {files?.file_sync_supported && !files.file_worker_healthy && <p className={styles.notice}>Worker nhập tệp chưa hoạt động.</p>}
            {retryMessage && <p className={styles.statusLabel} role="status">{retryMessage}</p>}
            {loading && <p className={styles.statusLabel}>Đang tải...</p>}
            {files && files.items.length === 0 && <p className={styles.statusLabel}>{failedOnly ? 'Không có tệp đồng bộ lỗi.' : 'Chưa có phiên bản nguồn.'}</p>}
            {files && files.items.length > 0 && (
                <div className={styles.tableWrap}>
                    <table className={`${styles.fileTable} ${styles.importTable}`}>
                        <colgroup>
                            <col style={{ width: '27%' }} />
                            <col style={{ width: '9%' }} />
                            <col style={{ width: '11%' }} />
                            <col style={{ width: '12%' }} />
                            <col style={{ width: '7%' }} />
                            <col style={{ width: '22%' }} />
                            <col style={{ width: '12%' }} />
                        </colgroup>
                        <thead><tr><th>Học liệu</th><th>Phiên bản</th><th>Nhập tệp</th><th>OCR/ETL</th><th>Lần thử</th><th>Lỗi</th><th>Thao tác</th></tr></thead>
                        <tbody>
                            {files.items.map((file) => (
                                <tr key={file.source_version_id}>
                                    <td><span className={styles.fileName} title={file.resource_title ?? file.resource_id}>{file.resource_title ?? file.resource_id}</span>
                                        <details className={styles.fileDetails}><summary>Nguồn / đích</summary>
                                            <div className={styles.fileMapping}>
                                                <span>SOURCE: {file.source_version_id}</span>
                                                <span>Master: {file.target_version_id || 'Chưa nhập'}</span>
                                                {file.target_version_id && <Link to={`/library/processing?${new URLSearchParams({ resourceId: file.resource_id, versionId: file.target_version_id, ...(file.processing_job_id ? { jobId: file.processing_job_id } : {}) })}`}>Xem xử lý</Link>}
                                            </div>
                                        </details>
                                    </td>
                                    <td>{file.source_version_number}</td>
                                    <td>{importLabels[file.import_status] ?? file.import_status}</td>
                                    <td>{file.etl_status ? etlLabels[file.etl_status] ?? file.etl_status : '—'}</td>
                                    <td>{file.retry_count ?? '—'}</td>
                                    <td>{file.last_error_code ? errorLabels[file.last_error_code] ?? 'Lỗi xử lý, cần kiểm tra' : '—'}</td>
                                    <td>{file.retry_eligible && file.import_job_id ? <button className={`${styles.button} ${styles.fileAction}`}
                                        type="button" disabled={retryPending} onClick={() => void retry(file.import_job_id!)}>Thử lại</button> : '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {(cursors.length > 1 || files?.next_cursor) && (
                <div className={styles.pagination}>
                    <button className={styles.button} type="button" disabled={loading || cursors.length === 1}
                        onClick={() => changePage(cursors.slice(0, -1))}>Trước</button>
                    <span>Trang {cursors.length}</span>
                    <button className={styles.button} type="button" disabled={loading || !files?.next_cursor}
                        onClick={() => { if (files?.next_cursor) changePage([...cursors, files.next_cursor]) }}>Sau</button>
                </div>
            )}
        </section>
    )
}

type ConfirmationRequest = {
    message: string
    resolve: (accepted: boolean) => void
}

function useConfirmation() {
    const [request, setRequest] = useState<ConfirmationRequest | null>(null)
    const pendingRef = useRef<ConfirmationRequest | null>(null)

    useEffect(() => () => {
        pendingRef.current?.resolve(false)
        pendingRef.current = null
    }, [])

    const confirm = useCallback((message: string): Promise<boolean> => {
        if (pendingRef.current) return Promise.resolve(false)
        return new Promise((resolve) => {
            const next = { message, resolve }
            pendingRef.current = next
            setRequest(next)
        })
    }, [])

    function finish(accepted: boolean) {
        const pending = pendingRef.current
        pendingRef.current = null
        setRequest(null)
        pending?.resolve(accepted)
    }

    return {
        confirm,
        dialog: request ? <ConfirmationDialog message={request.message} onDecision={finish} /> : null,
    }
}

function ConfirmationDialog({ message, onDecision }: {
    message: string
    onDecision: (accepted: boolean) => void
}) {
    const dialogRef = useRef<HTMLDialogElement>(null)
    const titleId = useId()
    const messageId = useId()

    useEffect(() => {
        const element = dialogRef.current
        element?.showModal()
        return () => element?.close()
    }, [])

    return (
        <dialog ref={dialogRef} className={styles.confirmDialog} aria-labelledby={titleId}
            aria-describedby={messageId} onCancel={(event) => {
                event.preventDefault()
                onDecision(false)
            }}>
            <h2 id={titleId}>Xác nhận thao tác</h2>
            <p id={messageId}>{message}</p>
            <div className={styles.confirmActions}>
                <button type="button" className={`${styles.button} ${styles.cancelButton}`}
                    onClick={() => onDecision(false)}>Hủy</button>
                <button type="button" className={styles.button}
                    onClick={() => onDecision(true)}>Xác nhận</button>
            </div>
        </dialog>
    )
}

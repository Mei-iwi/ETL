import { useCallback, useEffect, useRef, useState } from 'react'
import { NoticeDialog } from '../../components/NoticeDialog'
import {
    getDiscrepancies,
    getLatestReconciliationReport,
    triggerReconciliation,
} from '../../services/reconciliationApi'
import type {
    DiscrepancyItem,
    ReconciliationLatestReport,
} from '../../types/reconciliation'
import styles from './DataReconciliationPage.module.css'

function formatBytes(bytes?: number | null): string {
    if (bytes === undefined || bytes === null || bytes === 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    let size = bytes
    let unitIndex = 0
    while (Math.abs(size) >= 1024 && unitIndex < units.length - 1) {
        size /= 1024
        unitIndex++
    }
    return `${size.toFixed(2)} ${units[unitIndex]}`
}

function formatDate(dateStr?: string | null): string {
    if (!dateStr) return '—'
    try {
        const d = new Date(dateStr)
        return d.toLocaleString('vi-VN', {
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
        })
    } catch {
        return dateStr
    }
}

const FLAG_LABELS: Record<string, { label: string; badgeClass: string; desc: string }> = {
    NOT_VERIFIED: { label: 'NOT_VERIFIED', badgeClass: styles.badgeWarning, desc: 'Chưa xác minh' },
    SYNCED: {
        label: 'SYNCED',
        badgeClass: styles.badgeSuccess,
        desc: 'Đồng nhất',
    },
    DESYNC_BLOB_SIZE: {
        label: 'DESYNC_BLOB_SIZE',
        badgeClass: styles.badgeWarning,
        desc: 'Lệch dung lượng',
    },
    DESYNC_CHECKSUM: {
        label: 'DESYNC_CHECKSUM',
        badgeClass: styles.badgeDanger,
        desc: 'Lệch SHA-256',
    },
    ORPHAN_SOURCE: {
        label: 'ORPHAN_SOURCE',
        badgeClass: styles.badgeInfo,
        desc: 'Mới ở Nguồn',
    },
    ORPHAN_MASTER: {
        label: 'ORPHAN_MASTER',
        badgeClass: styles.badgePurple,
        desc: 'Đã xóa ở Nguồn',
    },
    DESYNC_METADATA: {
        label: 'DESYNC_METADATA',
        badgeClass: styles.badgeGray,
        desc: 'Lệch Metadata',
    },
    MUTATED_DOWNSTREAM: {
        label: 'MUTATED_DOWNSTREAM',
        badgeClass: styles.badgeWarning,
        desc: 'Sửa ở Đích',
    },
}

export function DataReconciliationPage() {
    const [report, setReport] = useState<ReconciliationLatestReport | null>(null)
    const [discrepancies, setDiscrepancies] = useState<DiscrepancyItem[]>([])
    const [total, setTotal] = useState(0)
    const [page, setPage] = useState(1)
    const [pageSize] = useState(10)
    const [totalPages, setTotalPages] = useState(1)

    const [selectedStream, setSelectedStream] = useState<string>('all')
    const [selectedFlag, setSelectedFlag] = useState<string>('ALL')

    const [loading, setLoading] = useState(false)
    const [reconciling, setReconciling] = useState(false)
    const [error, setError] = useState('')
    const [notice, setNotice] = useState<{ title: string; message: string } | null>(null)
    const [automatic, setAutomatic] = useState(false)
    const running = useRef(false)
    const posting = useRef(false)
    const startedRun = useRef<string | null>(null)
    const loadRevision = useRef(0)

    const loadData = useCallback(async () => {
        const revision = ++loadRevision.current
        setLoading(true)
        setError('')
        try {
            // Lấy báo cáo mới nhất trước để lấy run_id của từng stream
            const latest = await getLatestReconciliationReport(selectedStream)
            if (revision !== loadRevision.current) return
            setReport(latest)
            const active = latest.stream_reports.some(row => row.status === 'RUNNING')
            running.current = active || posting.current
            setReconciling(active || posting.current)
            const completed = latest.stream_reports.find(row => row.run_id === startedRun.current && row.status !== 'RUNNING')
            if (completed) {
                startedRun.current = null
                setNotice({ title: 'Kết quả đối soát', message: completed.summary_meta?.reason === 'RECONCILIATION_INTERRUPTED'
                    ? 'Đối soát đã gián đoạn. Bấm Quét đối soát để ưu tiên phần chưa xử lý.'
                    : completed.status === 'COMPLETED'
                    ? 'Đã quét và xác minh xong kho tệp.'
                    : completed.status === 'FAILED' ? 'Đối soát bị gián đoạn. Tiến độ đã được lưu; có thể chạy lại.'
                    : 'Đã quét xong. Xem các tệp sai lệch hoặc chưa xác minh và lý do trong bảng chi tiết.' })
            }

            // Thu thập run_id của các stream trong báo cáo mới nhất
            const latestRunIds = latest.stream_reports
                ?.map((r) => r.run_id)
                .filter((id): id is string => Boolean(id))

            // Chỉ lấy discrepancies thuộc các lần chạy mới nhất (lọc theo run_id)
            if (latestRunIds && latestRunIds.length > 0) {
                const result = await getDiscrepancies({ latestOnly: true, stream: selectedStream,
                    flag: selectedFlag, page, pageSize })
                if (revision !== loadRevision.current) return
                setDiscrepancies(result.items)
                setTotal(result.total)
                setTotalPages(result.total_pages)
            } else {
                setDiscrepancies([])
                setTotal(0)
                setTotalPages(0)
            }
        } catch (err) {
            if (revision === loadRevision.current) setError(err instanceof Error ? err.message : 'Không thể tải báo cáo đối soát')
        } finally {
            if (revision === loadRevision.current) setLoading(false)
        }
    }, [selectedStream, selectedFlag, page, pageSize])

    useEffect(() => {
        void loadData()
        return () => { loadRevision.current++ }
    }, [loadData])

    useEffect(() => {
        if (!reconciling) return
        const timer = window.setInterval(() => { void loadData() }, 2000)
        return () => window.clearInterval(timer)
    }, [reconciling, loadData])

    const handleRunReconcile = useCallback(async () => {
        if (running.current) return
        running.current = true
        posting.current = true
        setReconciling(true)
        setError('')
        try {
            const result = await triggerReconciliation(selectedStream, true)
            if (result.status === 'RUNNING') {
                startedRun.current = result.reports.find(row => row.stream_name === 'files')?.run_id ?? null
                setPage(1)
                await loadData()
                return
            }
            setNotice({ title: 'Kết quả đối soát', message: result.status === 'BLOCKED'
                ? 'Đã quét xong. Có sai lệch vượt ngưỡng an toàn; dữ liệu chưa được thay đổi.'
                : result.reports.some(report => report.status === 'PARTIAL')
                    ? 'Đã đối soát. Một phần dữ liệu chưa đủ điều kiện xác minh; tệp SOURCE chưa được đọc kiểm tra sẽ không tính là toàn vẹn.'
                    : 'Đã quét xong đối soát. Dữ liệu nghiệp vụ không thay đổi.' })
            setPage(1)
            await loadData()
        } catch (err) {
            setNotice({ title: 'Không thể đối soát', message: err instanceof Error ? err.message : 'Lỗi khi kích hoạt đối soát' })
        } finally {
            posting.current = false
            if (!startedRun.current) {
                setReconciling(false)
                running.current = false
            }
        }
    }, [selectedStream, loadData])

    useEffect(() => {
        if (!automatic) return
        const timer = window.setInterval(() => {
            if (document.visibilityState === 'visible' && !running.current) void handleRunReconcile()
        }, 300_000)
        return () => window.clearInterval(timer)
    }, [automatic, handleRunReconcile])

    const hasBlockedRun = report?.stream_reports?.some((r) => r.status === 'BLOCKED')
    const blockedReason = report?.stream_reports?.find((r) => r.summary_meta?.reason)?.summary_meta?.reason
    const unverified = report?.stream_reports.reduce((total, row) => total +
        (row.summary_meta?.unverified_source_files ?? 0) + (row.summary_meta?.unverified_metadata_records ?? 0), 0) ?? 0
    const onlyUnverified = unverified > 0 && report?.total_matched_records === 0 && report.total_mismatched_records === 0
    const fileProgress = report?.stream_reports.find(row => row.stream_name === 'files')?.summary_meta

    return (
        <div className={styles.container}>
            {notice && <NoticeDialog {...notice} onClose={() => setNotice(null)} />}
            {/* THẺ ĐIỀU KHIỂN & BỘ LỌC */}
            <div className={styles.card}>
                <div className={styles.sectionHeader}>
                    <div>
                        <h2 className={styles.sectionTitle}>Đối soát dữ liệu</h2>
                    </div>

                    <div className={styles.actionsBar}>
                        <select
                            className={styles.select}
                            value={selectedStream}
                            disabled={reconciling || loading}
                            onChange={(e) => {
                                setSelectedStream(e.target.value)
                                setPage(1)
                            }}
                            aria-label="Chọn luồng dữ liệu"
                        >
                            <option value="all">Tất cả luồng dữ liệu</option>
                            <option value="resources">Học liệu</option>
                            <option value="files">Kho tệp</option>
                            <option value="subjects">Môn học</option>
                            <option value="grades">Khối lớp</option>
                            <option value="users">Người dùng</option>
                        </select>

                        <button
                            type="button"
                            className={styles.primaryButton}
                            disabled={reconciling || loading}
                            onClick={() => void handleRunReconcile()}
                        >
                            {reconciling ? 'Đang đối soát...' : 'Quét đối soát'}
                        </button>

                        <label><input type="checkbox" checked={automatic} onChange={event => setAutomatic(event.target.checked)} /> Tự quét mỗi 5 phút khi mở trang</label>
                    </div>
                </div>

                {/* THÔNG BÁO / CẢNH BÁO */}
                {error && <div className={`${styles.alertBox} ${styles.alertWarning}`}>{error}</div>}
                {reconciling && <p role="status">Đang đối soát nền: {fileProgress?.processed_files ?? 0}/{fileProgress?.total_files ?? 0} tệp.
                    Có thể rời trang; tiến độ được lưu sau từng tệp. Đã xác minh {formatBytes(fileProgress?.verified_bytes)}.</p>}
                {!reconciling && fileProgress?.reason === 'RECONCILIATION_INTERRUPTED' && <p role="status">
                    Lượt trước bị gián đoạn ở {fileProgress.processed_files ?? 0}/{fileProgress.total_files ?? 0} tệp.
                    Bấm Quét đối soát để ưu tiên phần chưa xử lý.</p>}

                {hasBlockedRun && (
                    <div className={`${styles.alertBox} ${styles.alertWarning}`}>
                        <strong>Cần kiểm tra:</strong> {blockedReason || 'Sai lệch vượt ngưỡng an toàn. Xem chi tiết trước khi đồng bộ lại.'}
                    </div>
                )}

                {/* DASHBOARD KPI TỔNG HỢP */}
                <div className={styles.statusGrid}>
                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel}>Tỷ lệ đồng nhất</span>
                        <span className={styles.statusValue} style={{ color: (report?.overall_match_rate_percent ?? 100) >= 90 ? '#166534' : '#991b1b' }}>
                            {onlyUnverified ? 'Chưa xác minh' : report?.latest_run_id ? `${report.overall_match_rate_percent}%` : '—'}
                        </span>
                        <span className={styles.statusSub}>
                            Trạng thái: <strong>{report?.overall_status ?? 'CHƯA CHẠY'}</strong>
                        </span>
                    </div>

                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel}>Tổng bản ghi</span>
                        <span className={styles.statusValue}>
                            {report?.total_source_records ?? 0} / {report?.total_target_records ?? 0}
                        </span>
                        <span className={styles.statusSub}>
                            Khớp: <strong style={{ color: '#166534' }}>{report?.total_matched_records ?? 0}</strong> | Lệch: <strong style={{ color: '#991b1b' }}>{report?.total_mismatched_records ?? 0}</strong>
                            {unverified > 0 && <> | Chưa xác minh: <strong>{unverified}</strong></>}
                        </span>
                    </div>

                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel}>Dung lượng tệp</span>
                        <span className={styles.statusValue}>
                            {formatBytes(report?.total_target_bytes)}
                        </span>
                        <span className={styles.statusSub}>
                            Nguồn: {formatBytes(report?.total_source_bytes)} | Đích: {formatBytes(report?.total_target_bytes)}
                        </span>
                    </div>

                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel}>Table Fingerprint</span>
                        <span className={styles.statusValue} style={{ fontSize: '13px', fontFamily: 'monospace' }}>
                            {report?.stream_reports?.[0]?.target_table_hash ? `${report.stream_reports[0].target_table_hash.substring(0, 16)}...` : 'Chưa băm'}
                        </span>
                        <span className={styles.statusSub}>
                            Lần chạy: {formatDate(report?.last_reconciled_at)}
                        </span>
                    </div>
                </div>
            </div>

            {/* BẢNG ĐỐI SOÁT SONG SONG CHI TIẾT */}
            <div className={styles.card}>
                <div className={styles.sectionHeader}>
                    <div>
                        <h3 className={styles.sectionTitle}>Chi tiết sai lệch</h3>
                    </div>
                </div>

                {/* TAB LỌC CỜ HIỆU */}
                <div className={styles.filterTabs}>
                    {[
                        { key: 'ALL', label: 'Tất cả' },
                        { key: 'DESYNC_BLOB_SIZE', label: 'Lệch dung lượng' },
                        { key: 'DESYNC_CHECKSUM', label: 'Lệch SHA-256' },
                        { key: 'ORPHAN_SOURCE', label: 'Mới ở Nguồn' },
                        { key: 'NOT_VERIFIED', label: 'Chưa xác minh' },
                        { key: 'ORPHAN_MASTER', label: 'Đã xóa ở Nguồn' },
                        { key: 'DESYNC_METADATA', label: 'Lệch Metadata' },
                    ].map((tab) => (
                        <button
                            key={tab.key}
                            type="button"
                            className={`${styles.filterTab} ${selectedFlag === tab.key ? styles.filterTabActive : ''}`}
                            onClick={() => {
                                setSelectedFlag(tab.key)
                                setPage(1)
                            }}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>

                {loading ? (
                    <div className={styles.emptyState}>Đang tải dữ liệu...</div>
                ) : discrepancies.length === 0 ? (
                    <div className={styles.emptyState}>
                        {report?.latest_run_id
                            ? 'Không có sai lệch theo bộ lọc đã chọn.'
                            : 'Chưa có kết quả đối soát. Hãy quét để kiểm tra.'}
                    </div>
                ) : (
                    <div className={styles.tableWrap}>
                        <table className={styles.table}>
                            <thead>
                                <tr>
                                    <th style={{ width: '18%' }}>Bản ghi</th>
                                    <th style={{ width: '31%' }}>Dữ liệu Nguồn</th>
                                    <th style={{ width: '31%' }}>Dữ liệu Đích</th>
                                    <th style={{ width: '12%' }}>Cờ hiệu</th>
                                    <th style={{ width: '8%' }}>Trạng thái</th>
                                </tr>
                            </thead>
                            <tbody>
                                {discrepancies.map((item) => {
                                    const flagInfo = FLAG_LABELS[item.discrepancy_flag] || {
                                        label: item.discrepancy_flag,
                                        badgeClass: styles.badgeGray,
                                        desc: item.discrepancy_flag,
                                    }
                                    const sVal = item.source_value || {}
                                    const tVal = item.target_value || {}

                                    return (
                                        <tr key={item.id}>
                                            <td>
                                                <div className={styles.cellTitle}>{item.record_id}</div>
                                                <div className={styles.cellMeta}>
                                                    Luồng: <strong>{item.stream_name}</strong>
                                                </div>
                                                <div className={styles.cellMeta}>
                                                    Phát hiện: {formatDate(item.detected_at)}
                                                </div>
                                            </td>

                                            <td>
                                                {item.source_value ? (
                                                    <div>
                                                        {sVal.title && <div className={styles.cellTitle}>{sVal.title}</div>}
                                                        {sVal.source_file_size !== undefined && (
                                                            <div className={styles.cellMeta}>
                                                                Dung lượng: <strong>{formatBytes(sVal.source_file_size)}</strong>
                                                            </div>
                                                        )}
                                                        {sVal.source_hash && (
                                                            <div className={styles.cellMeta}>
                                                                SHA-256: <span className={styles.codeSnippet}>{sVal.source_hash.substring(0, 16)}...</span>
                                                            </div>
                                                        )}
                                                        {sVal.email && <div className={styles.cellMeta}>Email: {sVal.email}</div>}
                                                    </div>
                                                ) : (
                                                    <span style={{ color: '#94a3b8', fontStyle: 'italic' }}>[Không có ở nguồn]</span>
                                                )}
                                            </td>

                                            <td>
                                                {item.target_value ? (
                                                    <div>
                                                        {tVal.title && <div className={styles.cellTitle}>{tVal.title}</div>}
                                                        {tVal.reason_code && <div className={styles.cellMeta}>
                                                            Lý do: <strong>{({
                                                                SOURCE_REFERENCE_DISABLED: 'Chưa bật truy xuất nguồn',
                                                                SOURCE_TEMP_POLICY_REQUIRED: 'Chưa bật tệp tạm',
                                                                SOURCE_HASH_MISMATCH: 'SHA-256 nguồn không khớp phiên bản đã đồng bộ',
                                                                SOURCE_VERSION_MUTATED: 'Thông tin phiên bản nguồn đã thay đổi',
                                                                FILE_NOT_FOUND: 'Không tìm thấy tệp tại nguồn',
                                                                SOURCE_UNAVAILABLE: 'Nguồn chưa khả dụng',
                                                                SOURCE_RESOLVER_UNAVAILABLE: 'Chưa cấu hình resolver nguồn',
                                                                BLOCKED_SOURCE_MAPPING: 'Chưa xác minh được tham chiếu nguồn',
                                                                BLOCKED_SOURCE_POLICY: 'Nguồn chưa cho phép truy xuất',
                                                                RECONCILIATION_READ_BUDGET: 'Tệp vượt thời gian xử lý; các tệp khác vẫn được quét',
                                                            } as Record<string, string>)[tVal.reason_code] ?? tVal.reason_code}</strong>
                                                        </div>}
                                                        {tVal.storage_file_size !== undefined && (
                                                            <div className={styles.cellMeta}>
                                                                Dung lượng đĩa: <strong>{formatBytes(tVal.storage_file_size)}</strong>
                                                                {tVal.size_delta_bytes !== undefined && (
                                                                    <span style={{ color: '#991b1b', marginLeft: '6px' }}>
                                                                        ({tVal.size_delta_bytes > 0 ? `+${formatBytes(tVal.size_delta_bytes)}` : `-${formatBytes(Math.abs(tVal.size_delta_bytes))}`})
                                                                    </span>
                                                                )}
                                                            </div>
                                                        )}
                                                        {(tVal.computed_hash || tVal.source_hash) && (
                                                            <div className={styles.cellMeta}>
                                                                SHA-256: <span className={styles.codeSnippet}>{(tVal.computed_hash || tVal.source_hash).substring(0, 16)}...</span>
                                                            </div>
                                                        )}
                                                        {tVal.file_exists === false && (
                                                            <div style={{ color: '#991b1b', fontSize: '11px', fontWeight: 600 }}>
                                                                [Mất tệp trên đĩa]
                                                            </div>
                                                        )}
                                                        {tVal.email && <div className={styles.cellMeta}>Email: {tVal.email}</div>}
                                                    </div>
                                                ) : (
                                                    <span style={{ color: '#94a3b8', fontStyle: 'italic' }}>[Chưa có ở đích]</span>
                                                )}
                                            </td>

                                            <td>
                                                <span className={`${styles.badge} ${flagInfo.badgeClass}`} title={flagInfo.desc}>
                                                    {flagInfo.label}
                                                </span>
                                            </td>

                                            <td>
                                                <span style={{ fontSize: '12px', fontWeight: 500, color: item.resolution_status === 'AUTO_HEALED' ? '#166534' : '#64748b' }}>
                                                    {item.resolution_status === 'AUTO_HEALED' ? 'Đã tự sửa' : 'Chưa xử lý'}
                                                </span>
                                            </td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>
                )}

                {/* PHÂN TRANG */}
                {total > 0 && (
                    <div className={styles.pagination}>
                        <span>
                            Hiển thị {discrepancies.length} / {total} bản ghi
                        </span>
                        <div style={{ display: 'flex', gap: '8px' }}>
                            <button
                                type="button"
                                className={styles.secondaryButton}
                                disabled={page <= 1}
                                onClick={() => setPage((p) => Math.max(1, p - 1))}
                            >
                                Trang trước
                            </button>
                            <span style={{ alignSelf: 'center', margin: '0 8px', fontSize: '12px' }}>
                                Trang {page} / {totalPages || 1}
                            </span>
                            <button
                                type="button"
                                className={styles.secondaryButton}
                                disabled={page >= totalPages}
                                onClick={() => setPage((p) => p + 1)}
                            >
                                Trang sau
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}

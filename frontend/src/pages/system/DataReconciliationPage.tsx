import { useCallback, useEffect, useRef, useState } from 'react'
import { NoticeDialog } from '../../components/NoticeDialog'
import {
    getDiscrepancies,
    getLatestReconciliationReport,
    queueAudit,
    getAuditJobs,
} from '../../services/reconciliationApi'
import type {
    DiscrepancyItem,
    ReconciliationLatestReport,
} from '../../types/reconciliation'
import styles from './DataReconciliationPage.module.css'
import { ReconciliationSchedule } from './ReconciliationSchedule'
import { auditJobInProgress, auditSummary, formatAuditBytes as formatBytes, pendingHash } from './reconciliationDisplay'
import { startVisiblePolling } from './visiblePolling'

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
    const running = useRef(false)
    const posting = useRef(false)
    const startedRun = useRef<string[]>([])
    const loadRevision = useRef(0)

    // CHẾ ĐỘ CHẠY & PHẠM VI ĐỐI SOÁT
    const [reconcileMode, setReconcileMode] = useState<'INCREMENTAL' | 'FULL'>('INCREMENTAL')
    const [lastExecutionTimeMs, setLastExecutionTimeMs] = useState<number | null>(null)

    // MODAL THÔNG BÁO GIỮA MÀN HÌNH
    const [confirmModal, setConfirmModal] = useState<{
        title: string
        message: string
        onConfirm: () => void
    } | null>(null)

    const loadData = useCallback(async (signal?: AbortSignal) => {
        const revision = ++loadRevision.current
        setLoading(true)
        setError('')
        try {
            // Lấy báo cáo mới nhất trước để lấy run_id của từng stream
            const [latest, queued] = await Promise.all([
                getLatestReconciliationReport(selectedStream, signal), getAuditJobs(selectedStream, signal),
            ])
            if (signal?.aborted || revision !== loadRevision.current) return
            setReport(latest)
            const active = queued.items.some(auditJobInProgress)
                || latest.stream_reports.some(row => row.status === 'RUNNING')
            running.current = active || posting.current
            setReconciling(active || posting.current)
            const completed = startedRun.current.map(id => queued.items.find(row => row.job_id === id))
            if (completed.length && completed.every(row => row && !auditJobInProgress(row))) {
                startedRun.current = []
                setNotice({ title: 'Kết quả đối soát', message: completed.some(row => row?.status === 'FAILED' || row?.status === 'BLOCKED')
                    ? 'Đối soát gặp lỗi hoặc bị chặn. Xem kết quả và lý do trước khi chạy lại.'
                    : 'Lượt đối soát đã kết thúc. Xem các bản ghi sai lệch hoặc chưa xác minh; quét nhanh không xác minh SHA-256.' })
            }

            // Thu thập run_id của các stream trong báo cáo mới nhất
            const latestRunIds = latest.stream_reports
                ?.map((r) => r.run_id)
                .filter((id): id is string => Boolean(id))

            // Chỉ lấy discrepancies thuộc các lần chạy mới nhất (lọc theo run_id)
            if (latestRunIds && latestRunIds.length > 0) {
                const result = await getDiscrepancies({ latestOnly: true, stream: selectedStream,
                    flag: selectedFlag, page, pageSize }, signal)
                if (signal?.aborted || revision !== loadRevision.current) return
                setDiscrepancies(result.items)
                setTotal(result.total)
                setTotalPages(result.total_pages)
            } else {
                setDiscrepancies([])
                setTotal(0)
                setTotalPages(0)
            }
        } catch (err) {
            if (!signal?.aborted && revision === loadRevision.current) setError(err instanceof Error ? err.message : 'Không thể tải báo cáo đối soát')
        } finally {
            if (!signal?.aborted && revision === loadRevision.current) setLoading(false)
        }
    }, [selectedStream, selectedFlag, page, pageSize])

    useEffect(() => {
        const polling = startVisiblePolling(loadData, () => running.current)
        return () => { polling.stop(); loadRevision.current++ }
    }, [loadData])

    const handleRunReconcile = useCallback(async (dryRun: boolean = true) => {
        if (running.current) return
        running.current = true
        posting.current = true
        setReconciling(true)
        setError('')
        try {
            const result = await queueAudit(selectedStream, reconcileMode, !dryRun)
            startedRun.current = result.items?.map(row => row.job_id) ?? (result.job_id ? [result.job_id] : [])
            setLastExecutionTimeMs(null)
            setNotice({ title: 'Đối soát', message: 'Đã xếp hàng đối soát. Worker sẽ xử lý nền theo hạn mức.' })
            setPage(1)
            await loadData()
        } catch (err) {
            setNotice({ title: 'Không thể đối soát', message: err instanceof Error ? err.message : 'Lỗi khi kích hoạt đối soát' })
        } finally {
            posting.current = false
            if (!startedRun.current.length) {
                setReconciling(false)
                running.current = false
            }
        }
    }, [selectedStream, reconcileMode, loadData])

    const hasBlockedRun = report?.stream_reports?.some((r) => r.status === 'BLOCKED')
    const blockedReason = report?.stream_reports?.find((r) => r.summary_meta?.reason)?.summary_meta?.reason
    const { unverified, hashPending, onlyHashPending, observedBytes, observedFiles } = auditSummary(report)
    const onlyUnverified = unverified > 0 && report?.total_matched_records === 0 && report.total_mismatched_records === 0
    const fileProgress = report?.stream_reports.find(row => row.stream_name === 'files')?.summary_meta
    const scanInProgress = reconciling || report?.overall_status === 'RUNNING'

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
                        {/* 1. CHỌN LUỒNG */}
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
                        {/* 2. BỘ CHỌN PHẠM VI ĐỐI SOÁT */}
                        <select
                            className={styles.select}
                            value={reconcileMode}
                            onChange={(e) => setReconcileMode(e.target.value as 'INCREMENTAL' | 'FULL')}
                            aria-label="Phạm vi đối soát"
                        >
                            <option value="INCREMENTAL">Incremental / tệp: kiểm tra nhanh</option>
                            <option value="FULL">Full / tệp: SHA-256</option>
                        </select>

                        {/* 3. NÚT ĐỐI SOÁT & PHỤC HỒI THỦ CÔNG */}
                        <button className={styles.primaryButton} disabled={reconciling || loading}
                            onClick={() => reconcileMode === 'FULL' ? setConfirmModal({
                                title: 'Xác nhận quét toàn diện',
                                message: 'Worker sẽ quét metadata toàn diện hoặc đọc tệp để kiểm SHA-256 theo hạn mức.',
                                onConfirm: () => { setConfirmModal(null); void handleRunReconcile() },
                            }) : void handleRunReconcile()}>Quét đối soát</button>
                        {selectedStream !== 'all' && selectedStream !== 'files' && <button className={styles.secondaryButton}
                            disabled={reconciling || loading} onClick={() => setConfirmModal({ title: 'Đồng bộ bản ghi lệch',
                                message: 'Quét luồng đã chọn rồi chỉ đồng bộ ID metadata lệch. Không xóa bản ghi hoặc ghi đè tệp sai hash.',
                                onConfirm: () => { setConfirmModal(null); void handleRunReconcile(false) } })}>Đồng bộ lệch</button>}
                        {/* BADGE HIỂN THỊ THỜI GIAN THỰC THI */}
                        {(lastExecutionTimeMs ?? report?.stream_reports?.[0]?.execution_time_ms ?? 0) > 0 && (
                            <div className={styles.executionBadge} title="Thời gian thực thi đợt quét gần nhất">
                                {lastExecutionTimeMs ?? report?.stream_reports?.[0]?.execution_time_ms} ms
                            </div>
                        )}
                    </div>
                </div>

                <ReconciliationSchedule />

                {/* THÔNG BÁO / CẢNH BÁO */}
                {error && <div className={`${styles.alertBox} ${styles.alertWarning}`}>{error}</div>}
                {reconciling && <p role="status">Đã xếp hàng hoặc đang đối soát nền. Có thể rời trang; kết quả sẽ cập nhật sau khi worker xử lý.</p>}
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
                        <span className={styles.statusValue} style={{ color: scanInProgress || onlyHashPending ? '#475569' : (report?.overall_match_rate_percent ?? 100) >= 90 ? '#166534' : '#991b1b' }}>
                            {scanInProgress ? 'Đang kiểm tra' : onlyHashPending ? 'Chưa kiểm tra SHA-256' : onlyUnverified ? 'Chưa xác minh' : report?.latest_run_id ? `${report.overall_match_rate_percent}%` : '—'}
                        </span>
                        <span className={styles.statusSub}>
                            Trạng thái: <strong>{onlyHashPending && report?.overall_status === 'PARTIAL' ? 'Quét nhanh' : report?.overall_status ?? 'CHƯA CHẠY'}</strong>
                        </span>
                    </div>

                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel} title="Số liệu của các lượt đối soát, không phải tiến độ hoàn thành">Bản ghi SOURCE / MASTER</span>
                        <span className={styles.statusValue}>
                            {report?.total_source_records ?? 0} / {report?.total_target_records ?? 0}
                        </span>
                        <span className={styles.statusSub}>
                            Khớp: <strong style={{ color: '#166534' }}>{report?.total_matched_records ?? 0}</strong> | Lệch: <strong style={{ color: '#991b1b' }}>{report?.total_mismatched_records ?? 0}</strong>
                            {hashPending > 0 && <> | Chờ SHA-256: <strong>{hashPending}</strong></>}
                            {unverified > hashPending && <> | Chưa xác minh: <strong>{unverified - hashPending}</strong></>}
                        </span>
                    </div>

                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel}>Dung lượng quan sát</span>
                        <span className={styles.statusValue}>
                            {observedFiles > 0 ? formatBytes(observedBytes) : 'Chưa có số liệu'}
                        </span>
                        <span className={styles.statusSub}>
                            {observedFiles > 0 ? `Đọc được metadata ${observedFiles} tệp` : 'Chưa có thống kê dung lượng tệp'}
                        </span>
                    </div>

                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel}>Table Fingerprint</span>
                        <span className={styles.statusValue} style={{ fontSize: '13px', fontFamily: 'monospace' }}>
                            {report?.stream_reports?.[0]?.target_table_hash ? `${report.stream_reports[0].target_table_hash.substring(0, 16)}...` : 'Chưa băm'}
                        </span>
                        <span className={styles.statusSub}>
                            Lần chạy: {formatDate(report?.last_reconciled_at)}
                            {(report?.stream_reports?.[0]?.execution_time_ms ?? 0) > 0 && (
                                <> • {report?.stream_reports[0]?.execution_time_ms}ms</>
                            )}
                        </span>
                    </div>
                </div>
            </div>

            {/* BẢNG ĐỐI SOÁT SONG SONG CHI TIẾT */}
            <div className={styles.card}>
                <div className={styles.sectionHeader}>
                    <div>
                        <h3 className={styles.sectionTitle}>Chi tiết đối soát</h3>
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
                                    const hashPendingOnly = pendingHash(item)
                                    const flagInfo = hashPendingOnly ? { label: 'Chưa kiểm tra hash', badgeClass: styles.badgeInfo,
                                        desc: 'Quét nhanh đã đọc metadata; chưa xác minh SHA-256' } : FLAG_LABELS[item.discrepancy_flag] || {
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
                                                    <span style={{ color: '#94a3b8', fontStyle: 'italic' }}>{item.discrepancy_flag === 'ORPHAN_MASTER' ? '[Không có ở nguồn]' : '[Chưa có metadata đối chiếu]'}</span>
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
                                                                SHA256_NOT_CHECKED: 'Quét nhanh chưa tính SHA-256; chọn Full để xác minh hash',
                                                            } as Record<string, string>)[tVal.reason_code] ?? tVal.reason_code}</strong>
                                                        </div>}
                                                        {tVal.storage_file_size !== undefined && (
                                                            <div className={styles.cellMeta}>
                                                                Dung lượng quan sát: <strong>{formatBytes(tVal.storage_file_size)}</strong>
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
                                                    {hashPendingOnly ? 'Chờ kiểm tra hash' : item.resolution_status === 'AUTO_HEALED' ? 'Đã tự sửa' : 'Chưa xử lý'}
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

            {/* MODAL THÔNG BÁO GIỮA MÀN HÌNH */}
            {confirmModal && (
                <div
                    className={styles.modalBackdrop}
                    onClick={() => setConfirmModal(null)}
                >
                    <div
                        className={styles.modalCard}
                        onClick={(e) => e.stopPropagation()}
                        role="dialog"
                        aria-modal="true"
                    >
                        <h3 className={styles.modalTitle}>{confirmModal.title}</h3>
                        <p className={styles.modalContent}>{confirmModal.message}</p>
                        <div className={styles.modalActions}>
                            <button
                                type="button"
                                className={styles.secondaryButton}
                                onClick={() => setConfirmModal(null)}
                            >
                                Hủy bỏ
                            </button>
                            <button
                                type="button"
                                className={styles.primaryButton}
                                onClick={confirmModal.onConfirm}
                            >
                                Xác nhận
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

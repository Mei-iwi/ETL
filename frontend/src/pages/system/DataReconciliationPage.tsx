import { useCallback, useEffect, useState } from 'react'
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
    const [message, setMessage] = useState('')

    const loadData = useCallback(async () => {
        setLoading(true)
        setError('')
        try {
            // Lấy báo cáo mới nhất trước để lấy run_id của từng stream
            const latest = await getLatestReconciliationReport(selectedStream)
            setReport(latest)

            // Thu thập run_id của các stream trong báo cáo mới nhất
            const latestRunIds = latest.stream_reports
                ?.map((r) => r.run_id)
                .filter((id): id is string => Boolean(id))

            // Chỉ lấy discrepancies thuộc các lần chạy mới nhất (lọc theo run_id)
            if (latestRunIds && latestRunIds.length > 0) {
                const results = await Promise.all(
                    latestRunIds.map((runId) =>
                        getDiscrepancies({
                            runId,
                            stream: selectedStream,
                            flag: selectedFlag,
                            page: 1,
                            pageSize: 100,
                        }),
                    ),
                )
                // Gộp kết quả và phân trang phía client
                const allItems = results.flatMap((r) => r.items)
                const totalCount = allItems.length
                const start = (page - 1) * pageSize
                const pageItems = allItems.slice(start, start + pageSize)
                setDiscrepancies(pageItems)
                setTotal(totalCount)
                setTotalPages(Math.ceil(totalCount / pageSize) || 0)
            } else {
                setDiscrepancies([])
                setTotal(0)
                setTotalPages(0)
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Không thể tải báo cáo đối soát')
        } finally {
            setLoading(false)
        }
    }, [selectedStream, selectedFlag, page, pageSize])

    useEffect(() => {
        void loadData()
    }, [loadData])

    async function handleRunReconcile(dryRun: boolean) {
        if (!dryRun) {
            const ok = window.confirm('Xác nhận chạy tự phục hồi dữ liệu?')
            if (!ok) return
        }

        setReconciling(true)
        setError('')
        setMessage('')
        try {
            const result = await triggerReconciliation(selectedStream, dryRun)
            setMessage(
                result.status === 'BLOCKED'
                    ? 'Đợt đối soát đã bị khóa do sai lệch vượt ngưỡng 10%.'
                    : dryRun
                      ? 'Đã quét xong đối soát (Dry-run).'
                      : 'Đã hoàn tất tự phục hồi dữ liệu.',
            )
            setPage(1)
            await loadData()
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Lỗi khi kích hoạt đối soát')
        } finally {
            setReconciling(false)
        }
    }

    const hasBlockedRun = report?.stream_reports?.some((r) => r.status === 'BLOCKED')
    const blockedReason = report?.stream_reports?.find((r) => r.summary_meta?.reason)?.summary_meta?.reason

    return (
        <div className={styles.container}>
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
                            onClick={() => void handleRunReconcile(true)}
                        >
                            {reconciling ? 'Đang đối soát...' : 'Quét đối soát'}
                        </button>

                        <button
                            type="button"
                            className={styles.secondaryButton}
                            disabled={reconciling || loading}
                            onClick={() => void handleRunReconcile(false)}
                        >
                            Tự phục hồi
                        </button>
                    </div>
                </div>

                {/* THÔNG BÁO / CẢNH BÁO */}
                {error && <div className={`${styles.alertBox} ${styles.alertWarning}`}>{error}</div>}
                {message && <div className={`${styles.alertBox} ${styles.alertSuccess}`}>{message}</div>}

                {hasBlockedRun && (
                    <div className={`${styles.alertBox} ${styles.alertWarning}`}>
                        <strong>Khóa an toàn:</strong> {blockedReason || 'Tỷ lệ sai lệch vượt ngưỡng 10%, đã tạm dừng thao tác tự sửa.'}
                    </div>
                )}

                {/* DASHBOARD KPI TỔNG HỢP */}
                <div className={styles.statusGrid}>
                    <div className={styles.statusItem}>
                        <span className={styles.statusLabel}>Tỷ lệ đồng nhất</span>
                        <span className={styles.statusValue} style={{ color: (report?.overall_match_rate_percent ?? 100) >= 90 ? '#166534' : '#991b1b' }}>
                            {report ? `${report.overall_match_rate_percent}%` : '—'}
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
                        {report?.total_mismatched_records === 0
                            ? 'Dữ liệu hai bên đồng nhất. Không có sai lệch.'
                            : 'Không có bản ghi phù hợp.'}
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

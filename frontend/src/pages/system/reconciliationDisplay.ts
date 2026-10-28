import type { DiscrepancyItem, ReconciliationLatestReport } from '../../types/reconciliation'

export function formatAuditBytes(bytes?: number | null): string {
    if (bytes === undefined || bytes === null) return 'Chưa có số liệu'
    if (bytes === 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    let size = bytes, unit = 0
    while (Math.abs(size) >= 1024 && unit < units.length - 1) { size /= 1024; unit++ }
    return `${size.toFixed(2)} ${units[unit]}`
}

export function pendingHash(item: DiscrepancyItem) {
    return item.discrepancy_flag === 'NOT_VERIFIED'
        && item.target_value?.verification === 'SIZE_ONLY'
        && item.target_value?.reason_code === 'SHA256_NOT_CHECKED'
        && item.target_value?.size_status !== 'SIZE_MISMATCH'
}

export function auditSummary(report: ReconciliationLatestReport | null) {
    let unverified = 0, hashPending = 0, observedBytes = 0, observedFiles = 0
    for (const row of report?.stream_reports ?? []) {
        const meta = row.summary_meta
        unverified += row.stream_name === 'files'
            ? meta?.unverified_source_files ?? meta?.not_verified_count ?? 0
            : meta?.unverified_metadata_records ?? meta?.not_verified_count ?? 0
        if (row.stream_name === 'files') {
            hashPending += meta?.sha256_not_checked_count ?? meta?.verification_reason_counts?.SHA256_NOT_CHECKED ?? 0
            observedBytes += meta?.observed_file_bytes ?? 0
            observedFiles += meta?.observed_file_count ?? 0
        }
    }
    return { unverified, hashPending, observedBytes, observedFiles,
        onlyHashPending: unverified > 0 && hashPending === unverified && report?.total_mismatched_records === 0
            && ['PARTIAL', 'COMPLETED'].includes(report.overall_status)
            && report.stream_reports.every(row => row.stream_name === 'files'
                ? ['PARTIAL', 'COMPLETED'].includes(row.status) && !row.summary_meta?.reason
                    && row.summary_meta?.coverage_status !== 'COVERAGE_BEHIND'
                : row.status === 'COMPLETED') }
}

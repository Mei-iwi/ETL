import type { DiscrepancyItem, ReconciliationLatestReport } from '../../types/reconciliation'
import type { AuditCoverage, AuditJob } from '../../services/reconciliationApi'

export function sha256CoverageLabel(coverage: AuditCoverage | null) {
    if (!coverage || !Number.isInteger(coverage.total_files) || coverage.total_files < 0
        || !Number.isInteger(coverage.recent_sha256_evidence_count)
        || coverage.recent_sha256_evidence_count < 0
        || coverage.recent_sha256_evidence_count > coverage.total_files) return 'Chưa đủ dữ liệu xác minh SHA-256'
    if (!coverage.total_files) return 'Chưa có tệp'
    const verified = coverage.recent_sha256_evidence_count
    // Never round partial coverage up to 100%.
    const percent = Math.floor(verified / coverage.total_files * 1000) / 10
    return `${verified}/${coverage.total_files} tệp (${percent}%)`
}

export function metadataMatchLabel(report: ReconciliationLatestReport | null) {
    const rows = report?.stream_reports.filter(row => row.stream_name !== 'files') ?? []
    const total = rows.reduce((sum, row) => sum + Math.max(row.source_count, row.target_count), 0)
    const matched = rows.reduce((sum, row) => sum + row.matched_count, 0)
    if (!total || !Number.isFinite(total) || !Number.isFinite(matched) || matched < 0 || matched > total) return '—'
    return `${Math.floor(matched / total * 1000) / 10}%`
}

export function auditJobInProgress(job: AuditJob) {
    if (['PENDING', 'RUNNING', 'RETRY_WAIT'].includes(job.status)) return true
    const progress = job.progress
    return job.status === 'PARTIAL' && progress.manual_full_scan === true
        && progress.coverage_status === 'COVERAGE_BEHIND' && progress.cycle_finished !== true
        && typeof progress.last_id === 'string' && progress.last_id !== progress.manual_batch_start
}

export function auditViewJobs(jobs: AuditJob[], requested: string[] = []) {
    if (requested.length) return jobs.filter(job => requested.includes(job.job_id))
    const seen = new Set<string>()
    return jobs.filter(job => {
        if (seen.has(job.stream)) return false
        seen.add(job.stream)
        return true
    })
}

export function auditJobLabel(job: AuditJob) {
    const labels: Record<string, string> = { PENDING: 'Đang chờ', RUNNING: 'Đang chạy', RETRY_WAIT: 'Chờ thử lại',
        COMPLETED: 'Hoàn tất', FAILED: 'Lỗi', BLOCKED: 'Bị chặn', CANCELLED: 'Đã hủy' }
    const status = job.status === 'PARTIAL' ? auditJobInProgress(job) ? 'Chờ batch tiếp' : 'Chưa xác minh hết'
        : labels[job.status] ?? job.status
    return `${job.audit_type === 'DEEP' ? 'DEEP SHA-256' : job.audit_type === 'LIGHT' ? 'LIGHT (quét nhanh)' : job.audit_type} · ${status}`
}

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
            ? (meta?.unverified_source_files ?? meta?.not_verified_count ?? 0)
                + (meta?.manual_full_scan ? meta.cycle_not_verified_count ?? 0 : 0)
            : meta?.unverified_metadata_records ?? meta?.not_verified_count ?? 0
        if (row.stream_name === 'files') {
            hashPending += meta?.sha256_not_checked_count ?? meta?.verification_reason_counts?.SHA256_NOT_CHECKED ?? 0
            observedBytes += (meta?.observed_file_bytes ?? 0) + (meta?.manual_full_scan ? meta.cycle_observed_file_bytes ?? 0 : 0)
            observedFiles += (meta?.observed_file_count ?? 0) + (meta?.manual_full_scan ? meta.cycle_observed_file_count ?? 0 : 0)
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

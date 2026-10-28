import assert from 'node:assert/strict'
import test from 'node:test'
import { auditJobInProgress, auditJobLabel, auditSummary, auditViewJobs, formatAuditBytes, metadataMatchLabel, pendingHash, sha256CoverageLabel } from '../src/pages/system/reconciliationDisplay.ts'

const pending = { discrepancy_flag: 'NOT_VERIFIED', target_value: {
    verification: 'SIZE_ONLY', reason_code: 'SHA256_NOT_CHECKED', size_status: 'SIZE_MATCHED', storage_file_size: 5525995,
} }

test('SHA coverage uses current per-file proof instead of LIGHT job counts', () => {
    assert.equal(sha256CoverageLabel({ total_files: 38, recent_sha256_evidence_count: 38 }), '38/38 tệp (100%)')
    assert.equal(sha256CoverageLabel({ total_files: 38, recent_sha256_evidence_count: 19 }), '19/38 tệp (50%)')
    assert.equal(sha256CoverageLabel({ total_files: 38, recent_sha256_evidence_count: 0 }), '0/38 tệp (0%)')
    assert.equal(sha256CoverageLabel({ total_files: 10000, recent_sha256_evidence_count: 9999 }), '9999/10000 tệp (99.9%)')
    assert.equal(sha256CoverageLabel({ total_files: 0, recent_sha256_evidence_count: 0 }), 'Chưa có tệp')
    for (const value of [null, {}, { total_files: 2 }, { total_files: 2, recent_sha256_evidence_count: 3 }])
        assert.equal(sha256CoverageLabel(value), 'Chưa đủ dữ liệu xác minh SHA-256')
})

test('metadata percentage excludes files and labels only the scanned metadata scope', () => {
    assert.equal(metadataMatchLabel({ stream_reports: [
        { stream_name: 'files', source_count: 38, target_count: 38, matched_count: 0 },
        { stream_name: 'subjects', source_count: 10, target_count: 10, matched_count: 9 },
    ] }), '90%')
    assert.equal(metadataMatchLabel(null), '—')
    assert.equal(metadataMatchLabel({ stream_reports: [{ stream_name: 'files' }] }), '—')
})
function report(meta, extras = []) {
    return { overall_status: 'PARTIAL', total_mismatched_records: 0,
        stream_reports: [{ stream_name: 'files', status: 'PARTIAL', summary_meta: meta }, ...extras] }
}

test('legacy quick scan pending hashes are distinct from failed verification', () => {
    const result = auditSummary(report({ unverified_source_files: 38, verification_reason_counts: { SHA256_NOT_CHECKED: 38 } }))
    assert.equal(result.onlyHashPending, true)
    assert.equal(result.hashPending, 38)
    assert.equal(result.observedFiles, 0) // No invented aggregate for historical reports.
    assert.equal(pendingHash(pending), true)
})

test('metadata-only observations retain bytes without claiming hash matches', () => {
    const result = auditSummary(report({ not_verified_count: 1, sha256_not_checked_count: 1,
        observed_file_bytes: 5525995, observed_file_count: 1 }))
    assert.equal(result.unverified, 1)
    assert.equal(result.observedBytes, 5525995)
    assert.equal(result.onlyHashPending, true)
})

test('real errors, interrupted scans and size mismatches cannot be hidden as pending hashes', () => {
    const meta = { not_verified_count: 1, sha256_not_checked_count: 1 }
    assert.equal(auditSummary(report(meta, [{ stream_name: 'users', status: 'FAILED' }])).onlyHashPending, false)
    assert.equal(auditSummary(report({ ...meta, reason: 'RECONCILIATION_BUDGET' })).onlyHashPending, false)
    assert.equal(auditSummary(report({ ...meta, coverage_status: 'COVERAGE_BEHIND' })).onlyHashPending, false)
    assert.equal(pendingHash({ ...pending, discrepancy_flag: 'DESYNC_BLOB_SIZE' }), false)
    assert.equal(pendingHash({ ...pending, target_value: { ...pending.target_value, reason_code: 'SOURCE_UNAVAILABLE' } }), false)
})

test('mixed metadata verification counts are not lost or double-counted', () => {
    const result = auditSummary(report({ not_verified_count: 3, sha256_not_checked_count: 3 }, [
        { stream_name: 'users', status: 'PARTIAL', summary_meta: { unverified_source_files: 0,
            unverified_metadata_records: 2, not_verified_count: 2 } },
    ]))
    assert.equal(result.unverified, 5)
    assert.equal(result.onlyHashPending, false)
})

test('unknown file sizes remain unknown while genuinely empty files remain zero bytes', () => {
    assert.equal(formatAuditBytes(null), 'Chưa có số liệu')
    assert.equal(formatAuditBytes(undefined), 'Chưa có số liệu')
    assert.equal(formatAuditBytes(0), '0 B')
    assert.equal(formatAuditBytes(5525995), '5.27 MB')
})

test('manual full scan stays active across a budget boundary but not after finishing or no progress', () => {
    const job = { status: 'PARTIAL', progress: { manual_full_scan: true,
        coverage_status: 'COVERAGE_BEHIND', cycle_finished: false, last_id: 'v2', manual_batch_start: 'v1' } }
    assert.equal(auditJobInProgress(job), true)
    assert.equal(auditJobInProgress({ ...job, progress: { ...job.progress, cycle_finished: true } }), false)
    assert.equal(auditJobInProgress({ ...job, progress: { ...job.progress, manual_batch_start: 'v2' } }), false)
    assert.equal(auditJobInProgress({ ...job, status: 'FAILED' }), false)
})

test('full scan totals retain unverified files and measured observations from preceding batches', () => {
    const result = auditSummary(report({ manual_full_scan: true, not_verified_count: 0,
        cycle_not_verified_count: 1, observed_file_bytes: 0, cycle_observed_file_bytes: 32,
        observed_file_count: 0, cycle_observed_file_count: 2 }))
    assert.equal(result.unverified, 1)
    assert.equal(result.observedBytes, 32)
    assert.equal(result.observedFiles, 2)
    assert.equal(result.onlyHashPending, false)
})

test('view selection follows requested IDs or the newest job per stream', () => {
    const deep = { job_id: 'deep', stream: 'files', audit_type: 'DEEP', status: 'PENDING', progress: {} }
    const light = { job_id: 'light', stream: 'files', audit_type: 'LIGHT', status: 'PARTIAL', progress: {} }
    const metadata = { job_id: 'metadata', stream: 'subjects', audit_type: 'METADATA', status: 'COMPLETED', progress: {} }
    assert.deepEqual(auditViewJobs([deep, light, metadata]), [deep, metadata])
    assert.deepEqual(auditViewJobs([deep, light, metadata], ['deep']), [deep])
    assert.deepEqual(auditViewJobs([light], ['deep']), [])
    assert.equal(auditJobLabel(deep), 'DEEP SHA-256 · Đang chờ')
    assert.match(auditJobLabel(light), /LIGHT \(quét nhanh\)/)
})

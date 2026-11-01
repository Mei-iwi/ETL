import type {
    DiscrepancyListResponse,
    ReconciliationLatestReport,
    ReconcileTriggerResponse,
} from '../types/reconciliation'

async function getErrorMessage(response: Response) {
    const body: unknown = await response.json().catch(() => null)
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
        return body.detail
    }
    if (body && typeof body === 'object' && 'detail' in body && body.detail && typeof body.detail === 'object'
        && 'message' in body.detail && typeof body.detail.message === 'string') return body.detail.message
    return `Yêu cầu đối soát thất bại: HTTP ${response.status}`
}

export async function triggerReconciliation(
    stream: string = 'all',
    dryRun: boolean = true,
    mode: 'INCREMENTAL' | 'FULL' = 'INCREMENTAL',
    signal?: AbortSignal,
): Promise<ReconcileTriggerResponse> {
    const response = await fetch('/api/v1/reconciliation/runs', {
        method: 'POST',
        signal,
        headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
        },
        body: JSON.stringify({ stream, dry_run: dryRun, mode }),
    })
    if (!response.ok) throw new Error(await getErrorMessage(response))
    return response.json()
}

export async function getLatestReconciliationReport(
    stream?: string,
    signal?: AbortSignal,
    runIds: string[] = [],
): Promise<ReconciliationLatestReport> {
    const params = new URLSearchParams()
    if (stream && stream !== 'all') params.set('stream', stream)
    for (const id of runIds) params.append('run_ids', id)
    const response = await fetch(`/api/v1/reconciliation/runs/latest?${params}`, {
        signal,
        cache: 'no-store',
        headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new Error(await getErrorMessage(response))
    return response.json()
}

export async function getDiscrepancies(
    params: {
        runId?: string
        runIds?: string[]
        stream?: string
        flag?: string
        page?: number
        pageSize?: number
        latestOnly?: boolean
    } = {},
    signal?: AbortSignal,
): Promise<DiscrepancyListResponse> {
    const query = new URLSearchParams({
        page: String(params.page ?? 1),
        page_size: String(params.pageSize ?? 10),
    })
    if (params.runId) query.set('run_id', params.runId)
    for (const id of params.runIds ?? []) query.append('run_ids', id)
    if (params.latestOnly) query.set('latest_only', 'true')
    if (params.stream && params.stream !== 'all') query.set('stream', params.stream)
    if (params.flag && params.flag !== 'ALL') query.set('discrepancy_flag', params.flag)

    const response = await fetch(`/api/v1/reconciliation/discrepancies?${query}`, {
        signal,
        cache: 'no-store',
        headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new Error(await getErrorMessage(response))
    return response.json()
}

export type AuditKind = 'METADATA' | 'IDS' | 'LIGHT' | 'DEEP'
export type AuditJob = {
    job_id: string; stream: string; audit_type: AuditKind; status: string
    error_code: string | null; created_at: string; next_retry_at: string | null
    progress: Record<string, unknown>
}
export type AuditCoverage = {
    total_files: number; recent_sha256_evidence_count: number; not_verified_count: number
    size_checked: number; mismatched: number; whole_repository_verified: boolean
    scheduler_enabled: boolean
    coverage_status?: string
    last_deep_verified_at?: string | null
    evidence_window_days?: number
}
async function auditRequest<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`/api/v1/reconciliation/${path}`, { cache: 'no-store', ...init })
    if (!response.ok) throw new Error(await getErrorMessage(response))
    return response.json()
}
export function getAuditJobs(stream: string, signal?: AbortSignal, after?: string | null, jobIds: string[] = []) {
    const query = new URLSearchParams({ size: '20', newest: 'true' })
    if (stream !== 'all') query.set('stream', stream)
    if (after) query.set('after', after)
    for (const id of jobIds) query.append('job_ids', id)
    return auditRequest<{ items: AuditJob[]; next_cursor: string | null }>(`audit-jobs?${query}`, { signal })
}
export function getAuditCoverage(signal?: AbortSignal) {
    return auditRequest<AuditCoverage>('coverage', { signal })
}
export type SourceWriteBackStatus = {
    enabled: boolean; writer_supported: boolean; mode: string; fields: string[]; default: 'disabled'
    expected_source_state_hash?: string | null
    create_supported?: boolean
    actor_mapping?: string | null
    version_create_supported?: boolean
    delete_supported?: boolean
    cloud_upload_supported?: boolean
    version_storage?: string | null
}
export function getSourceWriteBackStatus(signal?: AbortSignal) {
    return fetch('/api/v1/source-writeback/status', { signal, cache: 'no-store', headers: { Accept: 'application/json' } })
        .then(async response => { if (!response.ok) throw new Error(await getErrorMessage(response)); return response.json() as Promise<SourceWriteBackStatus> })
}
export function getResourceSourceWriteBackStatus(resourceId: string, signal?: AbortSignal) {
    return fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/source-writeback/status`, {
        signal, cache: 'no-store', headers: { Accept: 'application/json' },
    }).then(async response => {
        if (!response.ok) throw new Error(await getErrorMessage(response))
        return response.json() as Promise<SourceWriteBackStatus>
    })
}
export type SourceWriteBackResponse = {
    request_id: string | null
    status: 'DRY_RUN' | 'WRITEBACK_PENDING_APPROVAL' | 'WRITEBACK_PENDING' | 'WRITEBACK_PROCESSING' | 'WRITEBACK_APPLIED' | 'WRITEBACK_CONFLICT' | 'WRITEBACK_FAILED'
    resource_id: string
    fields: string[]
    writer_supported: boolean
    error_code?: string | null
}
export function requestSourceWriteBack(
    resourceId: string,
    payload: { title: string; expected_source_state_hash: string; idempotency_key: string; approve?: boolean },
    signal?: AbortSignal,
) {
    return fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/source-writeback`, {
        method: 'POST', signal, credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(async response => {
        if (!response.ok) throw new Error(await getErrorMessage(response))
        return response.json() as Promise<SourceWriteBackResponse>
    })
}
export type SourceDeleteResponse = {
    request_id: string
    status: 'SOURCE_DELETE_PENDING' | 'SOURCE_DELETE_PROCESSING' | 'SOURCE_DELETE_APPLIED' | 'SOURCE_DELETE_FAILED'
    operation: 'DELETE_RESOURCE'
    resource_id: string
    reconciliation_required: boolean
    error_code?: string | null
}
export function requestSourceDelete(
    resourceId: string,
    payload: { expected_source_state_hash: string; idempotency_key: string; approve?: boolean },
    signal?: AbortSignal,
) {
    return fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/source-delete`, {
        method: 'POST', signal, credentials: 'same-origin',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(async response => {
        if (!response.ok) throw new Error(await getErrorMessage(response))
        return response.json() as Promise<SourceDeleteResponse>
    })
}
export type SourceCreateVersionResponse = {
    request_id: string
    status: 'SOURCE_VERSION_PENDING' | 'SOURCE_VERSION_PROCESSING' | 'SOURCE_VERSION_APPLIED' | 'SOURCE_VERSION_FAILED'
    operation: 'CREATE_VERSION'
    resource_id: string
    source_version_id?: string | null
    workflow: string
    reconciliation_required: boolean
    error_code?: string | null
}
export function requestSourceVersion(
    resourceId: string,
    file: File,
    idempotencyKey: string,
    expectedSha256?: string,
    signal?: AbortSignal,
) {
    const form = new FormData()
    form.set('file', file, file.name)
    form.set('idempotency_key', idempotencyKey)
    if (expectedSha256) form.set('expected_sha256', expectedSha256)
    return fetch(`/api/v1/resources/${encodeURIComponent(resourceId)}/source-versions`, {
        method: 'POST', signal, credentials: 'same-origin',
        headers: { Accept: 'application/json' }, body: form,
    }).then(async response => {
        if (!response.ok) throw new Error(await getErrorMessage(response))
        return response.json() as Promise<SourceCreateVersionResponse>
    })
}
export function queueAudit(stream: string, mode: 'INCREMENTAL' | 'FULL', repair = false, kind?: AuditKind) {
    const all = stream === 'all'
    return auditRequest<{ job_id?: string; status: string; items?: Array<{ job_id: string }> }>(
        all ? 'audit-jobs/all' : 'audit-jobs', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(all ? { mode, repair } : { stream, mode, repair: stream !== 'files' && repair,
                audit_type: kind ?? (stream === 'files' ? mode === 'FULL' ? 'DEEP' : 'LIGHT' : 'METADATA') }),
        })
}
export type FileEvidence = {
    resource_version_id: string; status: string; reason_code?: string | null
    recent_sha256_evidence?: boolean; last_verified_at?: string | null
    verification_method?: string | null; next_due_at?: string | null
}
export function getFileEvidence(version: string, signal?: AbortSignal) {
    return auditRequest<FileEvidence>(`file-evidence/${encodeURIComponent(version)}`, { signal })
}

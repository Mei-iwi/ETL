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

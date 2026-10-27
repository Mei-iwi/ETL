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
): Promise<ReconciliationLatestReport> {
    const params = new URLSearchParams()
    if (stream && stream !== 'all') params.set('stream', stream)
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

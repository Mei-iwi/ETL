import type {
    MasterSyncRequest,
    MasterSyncMode,
    MasterSyncResponse,
    MasterSyncStatusResponse,
    SourceSyncFilesResponse,
    SourceFilesBatchRetryResponse,
} from '../types/masterSync'

async function getErrorMessage(response: Response) {
    const body: unknown = await response.json().catch(() => null)
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
        return body.detail
    }
    if (body && typeof body === 'object' && 'detail' in body && body.detail && typeof body.detail === 'object'
        && 'message' in body.detail && typeof body.detail.message === 'string') {
        return body.detail.message
    }
    return `Yêu cầu đồng bộ thất bại: HTTP ${response.status}`
}

export async function getSourceSyncFiles(
    after: string | null,
    signal?: AbortSignal,
    options: { size?: number; status?: 'failed' | 'failed-retryable' | 'failed-blocked'; includeTotal?: boolean } = {},
): Promise<SourceSyncFilesResponse> {
    const params = new URLSearchParams({ size: String(options.size ?? 20) })
    if (options.status) params.set('status', options.status)
    if (options.includeTotal) params.set('include_total', 'true')
    if (after) params.set('after', after)
    const response = await fetch(`/api/v1/master-data/sync/files?${params}`, {
        signal,
        cache: 'no-store',
        headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new Error(await getErrorMessage(response))
    return response.json() as Promise<SourceSyncFilesResponse>
}

export async function retrySourceFile(importJobId: string): Promise<void> {
    const response = await fetch(`/api/v1/master-data/sync/files/${encodeURIComponent(importJobId)}/retry`, {
        method: 'POST', headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new Error(await getErrorMessage(response))
}

export async function retryFailedSourceFiles(after: string | null): Promise<SourceFilesBatchRetryResponse> {
    const params = new URLSearchParams({ size: '100' })
    if (after) params.set('after', after)
    const response = await fetch(`/api/v1/master-data/sync/files/retry-failed?${params}`, {
        method: 'POST', headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new Error(await getErrorMessage(response))
    return response.json() as Promise<SourceFilesBatchRetryResponse>
}

export async function syncMasterData(
    request: MasterSyncRequest,
    signal?: AbortSignal,
): Promise<MasterSyncResponse> {
    const response = await fetch('/api/v1/master-data/sync', {
        method: 'POST',
        signal,
        headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
    })

    if (!response.ok) {
        throw new Error(await getErrorMessage(response))
    }
    return response.json() as Promise<MasterSyncResponse>
}

export async function getMasterSyncStatus(
    signal?: AbortSignal,
): Promise<MasterSyncStatusResponse> {
    const response = await fetch('/api/v1/master-data/sync/status', {
        signal,
        cache: 'no-store',
        headers: { Accept: 'application/json' },
    })

    if (!response.ok) {
        throw new Error(await getErrorMessage(response))
    }
    return response.json() as Promise<MasterSyncStatusResponse>
}

export async function configureMasterSyncAuto(
    enabled: boolean | undefined,
    stream: MasterSyncRequest['stream'] = 'all',
    syncMode?: MasterSyncMode,
): Promise<MasterSyncStatusResponse> {
    const response = await fetch('/api/v1/master-data/sync/auto', {
        method: 'PATCH',
        headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ enabled, stream, ...(syncMode ? { sync_mode: syncMode } : {}) }),
    })

    if (!response.ok) {
        throw new Error(await getErrorMessage(response))
    }
    return response.json() as Promise<MasterSyncStatusResponse>
}

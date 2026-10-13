import type {
    MasterSyncRequest,
    MasterSyncResponse,
    MasterSyncStatusResponse,
} from '../types/masterSync'

async function getErrorMessage(response: Response) {
    const body: unknown = await response.json().catch(() => null)
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
        return body.detail
    }
    return `Yêu cầu đồng bộ thất bại: HTTP ${response.status}`
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
        headers: { Accept: 'application/json' },
    })

    if (!response.ok) {
        throw new Error(await getErrorMessage(response))
    }
    return response.json() as Promise<MasterSyncStatusResponse>
}

export async function configureMasterSyncAuto(
    enabled: boolean,
    stream: MasterSyncRequest['stream'] = 'all',
): Promise<MasterSyncStatusResponse> {
    const response = await fetch('/api/v1/master-data/sync/auto', {
        method: 'PATCH',
        headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ enabled, stream }),
    })

    if (!response.ok) {
        throw new Error(await getErrorMessage(response))
    }
    return response.json() as Promise<MasterSyncStatusResponse>
}

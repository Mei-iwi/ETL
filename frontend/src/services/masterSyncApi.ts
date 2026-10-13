import type { MasterSyncRequest, MasterSyncResponse } from '../types/masterSync'

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

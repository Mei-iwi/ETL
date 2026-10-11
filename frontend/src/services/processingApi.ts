import type { ProcessResult } from "../types/processing";

async function getErrorMessage(response: Response) {
    const body: unknown = await response.json().catch(() => null)

    if (
        body && typeof body === 'object' && 'detail' in body &&
        typeof body.detail === 'string'
    ) {
        return body.detail
    }
    return `Yêu cầu thất bại: ${response.status}`
}


export async function startProcessing(
    versionId: string,
    signal?: AbortSignal
): Promise<ProcessResult> {
    const response = await fetch(
        `/api/v1/resource-versions/${encodeURIComponent(versionId)}/process`,
        {
            method: 'POST',
            signal,
            headers: {
                Accept: 'application/json',
            },
        },
    )
    if (!response.ok) {
        throw new Error(await getErrorMessage(response))
    }
    return response.json() as Promise<ProcessResult>
}

export async function retryProcessing(
    jobId: string,
    signal?: AbortSignal
): Promise<ProcessResult> {
    const response = await fetch(
        `/api/v1/processing-jobs/${encodeURIComponent(jobId)}/retry`,
        {
            method: 'POST',
            signal,
            headers: {
                Accept: 'application/json'
            },
        },
    )

    if (!response.ok) {
        throw new Error(await getErrorMessage(response))
    }
    return response.json() as Promise<ProcessResult>
}
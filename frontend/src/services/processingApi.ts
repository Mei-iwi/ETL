import type {
    ContentUnitsResult,
    ProcessingJobResult,
    ProcessResult,
} from "../types/processing";

export class ProcessingApiError extends Error {
    status: number

    constructor(message: string, status: number) {
        super(message)
        this.name = 'ProcessingApiError'
        this.status = status
    }
}

export function isRetryableProcessingError(error: unknown) {
    return !(error instanceof ProcessingApiError) || error.status >= 500
}

async function getErrorMessage(response: Response) {
    const body: unknown = await response.json().catch(() => null)

    if (
        body && typeof body === 'object' && 'detail' in body &&
        typeof body.detail === 'string'
    ) {
        return body.detail
    }

    if (
        body && typeof body === 'object' && 'detail' in body &&
        body.detail && typeof body.detail === 'object' &&
        'message' in body.detail && typeof body.detail.message === 'string'
    ) {
        return body.detail.message
    }

    const statusMessages: Record<number, string> = {
        403: 'Bạn không có quyền thực hiện yêu cầu này.',
        404: 'Không tìm thấy dữ liệu xử lý.',
        409: 'Trạng thái hiện tại không cho phép xử lý lại.',
    }

    if (statusMessages[response.status]) {
        return statusMessages[response.status]
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
        throw new ProcessingApiError(await getErrorMessage(response), response.status)
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
        throw new ProcessingApiError(await getErrorMessage(response), response.status)
    }
    return response.json() as Promise<ProcessResult>
}

export async function getProcessingJob(
    jobId: string,
    signal?: AbortSignal,
): Promise<ProcessingJobResult> {
    const response = await fetch(
        `/api/v1/processing-jobs/${encodeURIComponent(jobId)}`,
        {
            signal,
            headers: {
                Accept: 'application/json',
            },
        },
    )

    if (!response.ok) {
        throw new ProcessingApiError(await getErrorMessage(response), response.status)
    }

    return response.json() as Promise<ProcessingJobResult>
}

export async function getContentUnits(
    versionId: string,
    offset = 0,
    limit = 10,
    signal?: AbortSignal,
): Promise<ContentUnitsResult> {
    const params = new URLSearchParams({
        offset: String(offset),
        limit: String(limit),
    })
    const response = await fetch(
        `/api/v1/resource-versions/${encodeURIComponent(versionId)}/content-units?${params}`,
        {
            signal,
            headers: {
                Accept: 'application/json',
            },
        },
    )

    if (!response.ok) {
        throw new ProcessingApiError(await getErrorMessage(response), response.status)
    }

    return response.json() as Promise<ContentUnitsResult>
}

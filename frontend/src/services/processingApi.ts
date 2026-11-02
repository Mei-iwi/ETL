import type {
    ContentUnitsResult,
    ProcessingJobResult,
    ProcessResult,
} from "../types/processing";

export class ProcessingApiError extends Error {
    status: number
    code?: string

    constructor(message: string, status: number, code?: string) {
        super(message)
        this.name = 'ProcessingApiError'
        this.status = status
        this.code = code
    }
}

export function isRetryableProcessingError(error: unknown) {
    return !(error instanceof ProcessingApiError) || error.status >= 500
}

export type OriginalOCRPage = {
    job_id: string
    page_num: number
    total_pages: number
    status: string
    available: boolean
    text: string | null
    offset: number
    total_characters: number | null
}

export async function getOriginalOCRPage(versionId: string, jobId: string, page: number, offset = 0, signal?: AbortSignal): Promise<OriginalOCRPage> {
    const params = new URLSearchParams({ job_id: jobId, offset: String(offset), limit: '10000' })
    const response = await fetch(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/ocr-pages/${page}?${params}`, {
        signal, headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new ProcessingApiError(await getErrorMessage(response), response.status)
    const value: unknown = await response.json()
    if (!value || typeof value !== 'object' || !('job_id' in value) || value.job_id !== jobId ||
        !('page_num' in value) || value.page_num !== page ||
        !('offset' in value) || value.offset !== offset || !('total_pages' in value) ||
        !Number.isInteger(value.total_pages) || Number(value.total_pages) < page || Number(value.total_pages) > 2000 ||
        !('status' in value) || typeof value.status !== 'string' ||
        !('available' in value) || typeof value.available !== 'boolean' || !('text' in value) ||
        !(value.text === null || typeof value.text === 'string') || !('total_characters' in value) ||
        !(value.total_characters === null || (Number.isInteger(value.total_characters) && Number(value.total_characters) >= 0)) ||
        (value.available !== (typeof value.text === 'string')) ||
        (value.available !== (typeof value.total_characters === 'number')) ||
        (typeof value.text === 'string' && Array.from(value.text).length > 10000)) {
        throw new ProcessingApiError('Dữ liệu OCR trả về không hợp lệ.', 502)
    }
    return value as OriginalOCRPage
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
        const body: unknown = await response.clone().json().catch(() => null)
        const code = body && typeof body === 'object' && 'detail' in body &&
            body.detail && typeof body.detail === 'object' && 'code' in body.detail &&
            typeof body.detail.code === 'string' ? body.detail.code : undefined
        throw new ProcessingApiError(await getErrorMessage(response), response.status, code)
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

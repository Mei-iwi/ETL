import type {
    ContentUnitsResult,
    JobStatus,
    ProcessingJobResult,
    ProcessingStatus,
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

export function isProcessingPending(jobStatus: JobStatus, processingStatus: ProcessingStatus) {
    return jobStatus === 'PENDING' || jobStatus === 'RUNNING'
        || (jobStatus === 'COMPLETED' && ['OCR_PENDING', 'FINALIZATION_PENDING', 'FINALIZING'].includes(processingStatus))
}

export function intakeStatusLabel(status: unknown): string {
    const labels: Record<string, string> = {
        RECEIVED: 'Đã tiếp nhận - Chờ kiểm tra OCR', CHECKING_OCR: 'Đang kiểm tra OCR',
        OCR_QUEUED: 'Đã xếp hàng OCR', OCR_RUNNING: 'Đang nhận dạng OCR',
        WAITING_AUTHORIZATION: 'Chờ quyền xử lý', WAITING_SOURCE_MAPPING: 'Đang đồng bộ phiên bản',
        PROVENANCE_REQUIRED: 'Cần xác minh nguồn OCR', SOURCE_INVALID: 'Nguồn không còn hợp lệ',
        RETRY_WAIT: 'Chờ thử lại', PROCESSING_FAILED: 'Xử lý thất bại',
        OCR_PENDING: 'Đã xếp hàng OCR', OCR_FAILED: 'OCR thất bại',
        FINALIZATION_PENDING: 'Chờ chuẩn hóa nội dung', FINALIZING: 'Đang chuẩn hóa nội dung',
        FINALIZATION_FAILED: 'Chuẩn hóa nội dung thất bại',
        INDEXING: 'Đang tạo chỉ mục', INDEX_FAILED: 'Tạo chỉ mục thất bại',
        READY: 'Sẵn sàng tra cứu', CONTENT_READY: 'Đã xử lý nội dung',
        NOT_STARTED: 'Chưa yêu cầu xử lý', NO_EXTRACTABLE_CONTENT: 'Không có nội dung trích xuất',
    }
    return typeof status === 'string' ? labels[status] || 'Đang cập nhật trạng thái' : 'Đang cập nhật trạng thái'
}

export function intakeStatusTone(status: unknown): 'error' | 'success' | 'active' {
    if (['SOURCE_INVALID', 'PROCESSING_FAILED', 'INDEX_FAILED', 'OCR_FAILED', 'FINALIZATION_FAILED'].includes(String(status))) return 'error'
    return ['READY', 'CONTENT_READY'].includes(String(status)) ? 'success' : 'active'
}

export function ocrAuditMessage(status: unknown, reason?: unknown): string {
    if (status === 'WAITING_FOR_PROCESS_AUTHORIZATION') {
        const reasons: Record<string, string> = {
            SOURCE_OCR_VERSION_NOT_AUTHORIZED: 'Phiên bản nguồn chưa đủ điều kiện theo policy OCR tự động đã được phê duyệt.',
            SOURCE_OCR_LIFECYCLE_NOT_AUTHORIZED: 'Trạng thái duyệt của phiên bản nguồn chưa cho phép OCR.',
            SOURCE_OCR_EXECUTION_MAPPING_REQUIRED: 'Đang chờ hoàn tất mapping phiên bản nguồn để xử lý OCR.',
            SOURCE_OCR_ACTOR_CONTRACT_DENIED: 'Tài khoản dịch vụ chưa được phê duyệt để xử lý OCR trong phạm vi này.',
        }
        if (typeof reason === 'string' && reasons[reason]) return reasons[reason]
    }
    const messages: Record<string, string> = {
        CHECKING: 'Đang kiểm tra OCR và quyền xử lý tại nguồn.',
        RECHECK_REQUIRED: 'Đang chờ kiểm tra lại quyền và trạng thái OCR tại nguồn.',
        QUEUED: 'Đã xếp hàng OCR. Worker sẽ tự động tiếp tục xử lý.',
        WAITING_FOR_PROCESS_AUTHORIZATION: 'Đã kiểm tra OCR. Đang chờ quyền xử lý tài liệu.',
        WAITING_FOR_SOURCE_MAPPING: 'Đang chờ hoàn tất đồng bộ phiên bản nguồn.',
        ABSENT: 'Chưa có OCR. Đang chờ đưa vào xử lý.',
        PARTIAL: 'OCR chưa đầy đủ. Hệ thống đang kiểm tra các trang còn thiếu.',
        COMPLETE_VERIFIED: 'OCR đã được xác minh. Nội dung và chỉ mục được xử lý ở các bước tiếp theo.',
        UNVERIFIED: 'Có dữ liệu OCR chưa đủ bằng chứng xác minh. Chưa thể tái sử dụng.',
        INVALID_OR_REVOKED: 'Phiên bản nguồn đã thay đổi hoặc không còn được phép sử dụng.',
        RETRYABLE_ERROR: 'Chưa kiểm tra được OCR. Hệ thống sẽ thử lại.',
        RETRY_EXHAUSTED: 'Tự động xử lý đã dừng sau nhiều lần lỗi. Cần kiểm tra trước khi thử lại.',
    }
    return typeof status === 'string' ? messages[status] || '' : ''
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

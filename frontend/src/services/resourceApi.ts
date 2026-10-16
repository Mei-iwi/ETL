import type {
    UpdateResourceMetadataRequest,
    UpdateResourceMetadataResponse,
} from '../types/resourceMetadata'
import type { CreateResourceVersionResponse } from '../types/resourceVersion'

const STATUS_MESSAGES: Record<number, string> = {
    400: 'Thông tin metadata không hợp lệ.',
    404: 'Không tìm thấy học liệu cần cập nhật.',
    409: 'Không thể sửa metadata tại Master. Kiểm tra nguồn quản lý học liệu.',
    422: 'Dữ liệu gửi lên không hợp lệ.',
    503: 'Cơ sở dữ liệu không khả dụng.',
    500: 'Đã xảy ra lỗi hệ thống.',
}

export function metadataReadOnlyMessage(sourceOrigin: string | null | undefined): string | null {
    if (sourceOrigin === 'LOCAL') return null
    return sourceOrigin === 'POSTGRESQL'
        ? 'Học liệu được quản lý từ SOURCE. Vui lòng sửa tại nguồn và đồng bộ lại.'
        : 'Chưa xác minh nguồn quản lý học liệu. Không thể cập nhật metadata tại Master.'
}

async function readErrorMessage(
    response: Response,
    statusMessages = STATUS_MESSAGES,
): Promise<string> {
    const body: unknown = await response.json().catch(() => null)

    if (body && typeof body === 'object' && 'detail' in body) {
        const detail = body.detail
        if (
            detail &&
            typeof detail === 'object' &&
            'message' in detail &&
            typeof detail.message === 'string'
        ) {
            return detail.message
        }
        if (typeof detail === 'string') return detail
    }

    return statusMessages[response.status] ?? `Yêu cầu cập nhật thất bại (HTTP ${response.status}).`
}

export async function updateResourceMetadata(
    resourceId: string,
    payload: UpdateResourceMetadataRequest,
    signal?: AbortSignal,
): Promise<UpdateResourceMetadataResponse> {
    const id = resourceId.trim()
    const title = payload.title.trim()

    if (!id) throw new Error('Thiếu mã học liệu cần cập nhật.')
    if (!title) throw new Error('Tên học liệu không được để trống.')
    if (!payload.resource_type_code || !payload.provider_id) {
        throw new Error('Vui lòng chọn loại học liệu và đơn vị cung cấp.')
    }

    const response = await fetch(
        `/api/v1/resources/${encodeURIComponent(id)}/metadata`,
        {
            method: 'PATCH',
            signal,
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ ...payload, title }),
        },
    )

    if (!response.ok) throw new Error(await readErrorMessage(response))
    return response.json() as Promise<UpdateResourceMetadataResponse>
}

export async function createResourceVersion(
    resourceId: string,
    file: File,
    signal?: AbortSignal,
): Promise<CreateResourceVersionResponse> {
    const formData = new FormData()
    formData.append('file', file)

    const response = await fetch(
        `/api/v1/resource/${encodeURIComponent(resourceId)}/versions`,
        {
            method: 'POST',
            signal,
            headers: { Accept: 'application/json' },
            body: formData,
        },
    )

    if (!response.ok) {
        throw new Error(await readErrorMessage(response, {
            400: 'Tệp học liệu không hợp lệ.',
            404: 'Không tìm thấy học liệu cần cập nhật.',
            413: 'Tệp học liệu vượt quá dung lượng cho phép.',
            422: 'Dữ liệu tệp gửi lên không hợp lệ.',
            503: 'Không thể lưu phiên bản học liệu.',
        }))
    }
    return response.json() as Promise<CreateResourceVersionResponse>
}

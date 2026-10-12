import type {
    UpdateResourceMetadataRequest,
    UpdateResourceMetadataResponse,
} from '../types/resourceMetadata'

const STATUS_MESSAGES: Record<number, string> = {
    400: 'Thông tin metadata không hợp lệ.',
    404: 'Không tìm thấy học liệu cần cập nhật.',
    422: 'Dữ liệu gửi lên không hợp lệ.',
    503: 'Cơ sở dữ liệu không khả dụng.',
    500: 'Đã xảy ra lỗi hệ thống.',
}

async function readErrorMessage(response: Response): Promise<string> {
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

    return STATUS_MESSAGES[response.status] ?? `Không thể cập nhật metadata (HTTP ${response.status}).`
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

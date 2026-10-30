export type FileIndexStatus = { processing_status: string; index_status: string; metadata_only?: boolean }
export type SourceResolution = { version_id: string; resolution_status: 'available' | 'unavailable' | 'stale' | 'blocked'; content_availability: 'metadata-only' | 'authorized-indexed'; source_record_available: boolean }
export type SourceOpen = {
    master_resource_id: string; version_id: string | null
    status: 'EXTERNAL_VIEWER_AVAILABLE' | 'SOURCE_AUTH_REQUIRED' | 'METADATA_ONLY' | 'SOURCE_UNAVAILABLE'
        | 'SOURCE_OPEN_UNSUPPORTED' | 'VERSION_STALE' | 'ACCESS_DENIED'
    files: { source_file_id: string; file_name: string | null; mime_type: string | null
        revision: string | null; access_status: 'ALLOWED' | 'AUTH_REQUIRED' | 'DENIED' | 'UNAVAILABLE' | 'METADATA_ONLY'
        viewer_url: string | null }[]
}
export function trustedViewerHref(value: string | null | undefined) {
    if (!value) return null
    try {
        const url = new URL(value)
        return url.protocol === 'https:' && !url.username && !url.password ? url.href : null
    } catch { return null }
}
export function sourceResolutionAllowsFile(resolution: SourceResolution | null, selected: string) {
    return resolution?.version_id === selected && resolution.resolution_status === 'available'
        && resolution.source_record_available === true && resolution.content_availability === 'authorized-indexed'
}
export function canOpenFile(status: FileIndexStatus | null | undefined) {
    return status?.metadata_only !== true && status?.processing_status === 'CONTENT_READY' && status.index_status === 'READY'
}
export function fileErrorMessage(status: number) {
    return ({
        403: 'Bạn chưa có quyền xem tệp.',
        404: 'Không tìm thấy tệp hoặc chưa có quyền truy cập.',
        409: 'Tệp chưa sẵn sàng. Kiểm tra xử lý và chỉ mục.',
        413: 'Tệp vượt giới hạn dung lượng cho phép.',
        415: 'Định dạng tệp không hỗ trợ xem. Hãy kiểm tra nguồn.',
        502: 'Dịch vụ tệp đang gián đoạn. Vui lòng thử lại.',
        503: 'Nguồn tạm thời không khả dụng hoặc chưa được phép truy xuất. Vui lòng kiểm tra lại.',
    } as Record<number, string>)[status] || 'Không tải được tệp.'
}

export async function fileResponseError(response: Response): Promise<string> {
    const body: unknown = await response.json().catch(() => null)
    const code = body && typeof body === 'object' && 'detail' in body && body.detail
        && typeof body.detail === 'object' && 'code' in body.detail ? body.detail.code : null
    const messages: Record<string, string> = {
        PREVIEW_CONVERTER_UNAVAILABLE: 'Chưa có công cụ chuyển Word/PowerPoint sang PDF trên máy chủ. Vui lòng liên hệ quản trị viên.',
        PREVIEW_CONVERSION_FAILED: 'Không thể tạo bản xem PDF. Bạn vẫn có thể tải file gốc hoặc mở tại nguồn.',
        PREVIEW_TIMEOUT: 'Tạo bản xem PDF quá thời gian chờ. Hãy thử lại hoặc tải file gốc.',
        PREVIEW_BUSY: 'Hệ thống đang tạo nhiều bản xem PDF. Vui lòng thử lại sau.',
        PREVIEW_EXTERNAL_CONTENT: 'Tệp chứa nội dung liên kết bên ngoài chưa được phép dựng bản xem. Hãy tải file gốc để xem.',
        PREVIEW_INVALID_FILE: 'Không đọc được tệp để tạo bản xem. Hãy kiểm tra file gốc.',
        SOURCE_REFERENCE_DISABLED: 'Chức năng truy xuất tệp nguồn chưa được bật. Vui lòng liên hệ quản trị viên.',
        SOURCE_TEMP_POLICY_REQUIRED: 'Truy xuất tệp nguồn đang bị chặn bởi chính sách tệp tạm. Vui lòng liên hệ quản trị viên.',
        SOURCE_UNAVAILABLE: 'Không kết nối được nguồn. Vui lòng kiểm tra lại sau.',
        SOURCE_STORAGE_UNAVAILABLE: 'Kho tệp nguồn hiện không thể truy cập. Vui lòng kiểm tra lại sau.',
        SOURCE_TIMEOUT: 'Truy xuất nguồn quá thời gian chờ. Vui lòng thử lại.',
        SOURCE_BUSY: 'Hệ thống đang xử lý nhiều tệp nguồn. Vui lòng thử lại sau.',
        DRIVE_UNAVAILABLE: 'Không kết nối được Google Drive. Vui lòng kiểm tra lại sau.',
        DRIVE_TIMEOUT: 'Truy xuất Google Drive quá thời gian chờ. Vui lòng thử lại.',
        DRIVE_RATE_LIMITED: 'Google Drive đang giới hạn lượt truy cập. Vui lòng thử lại sau.',
        SOURCE_HASH_MISMATCH: 'Tệp nguồn đã thay đổi nội dung. Cần kiểm tra phiên bản nguồn.',
        SOURCE_VERSION_MUTATED: 'Tham chiếu hoặc phiên bản nguồn đã thay đổi. Cần kiểm tra đồng bộ.',
        SOURCE_REVISION_CHANGED: 'Nguồn thay đổi trong lúc truy xuất. Vui lòng kiểm tra lại.',
        DRIVE_FILE_CHANGED: 'Nguồn thay đổi trong lúc truy xuất. Vui lòng kiểm tra lại.',
        SOURCE_VERSION_MISSING: 'Tệp nguồn không còn tồn tại.',
        DRIVE_FILE_NOT_FOUND: 'Tệp nguồn không còn tồn tại.',
        FILE_NOT_FOUND: 'Tệp nguồn không còn tồn tại hoặc chưa có quyền truy cập.',
        DRIVE_PERMISSION_DENIED: 'Nguồn đã từ chối quyền truy cập.',
        PERMISSION_DENIED: 'Nguồn đã từ chối quyền truy cập.',
        DRIVE_CREDENTIALS_EXPIRED: 'Quyền kết nối nguồn đã hết hạn. Vui lòng liên hệ người quản trị.',
    }
    if (code === 'EXTERNAL_SOURCE_CONTENT_DISABLED')
        return 'Tài liệu nằm tại kho nguồn; ETL chỉ hỗ trợ metadata và viewer đã được nguồn xác minh.'
    return typeof code === 'string' && messages[code] ? messages[code] : fileErrorMessage(response.status)
}


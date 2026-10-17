export type FileIndexStatus = { processing_status: string; index_status: string }
export function canOpenFile(status: FileIndexStatus | null | undefined) {
    return status?.processing_status === 'CONTENT_READY' && status.index_status === 'READY'
}
export function fileErrorMessage(status: number) {
    return ({
        403: 'Bạn chưa có quyền xem tệp.',
        404: 'Không tìm thấy tệp hoặc chưa có quyền truy cập.',
        409: 'Tệp chưa sẵn sàng. Kiểm tra xử lý và chỉ mục.',
        413: 'Tệp vượt giới hạn dung lượng cho phép.',
        502: 'Dịch vụ tệp đang gián đoạn. Vui lòng thử lại.',
    } as Record<number, string>)[status] || 'Không tải được tệp.'
}


import type { SearchResponse } from '../../types/search'

export function searchEmptyState(result: Pick<SearchResponse, 'message' | 'branch_status' | 'readiness'>) {
    if (result.message === 'PUBLIC_ACCESS_UNCONFIRMED' || result.message === 'NO_ACCESSIBLE_CONTENT') {
        return { title: 'Chưa có kết quả trong phạm vi truy cập', message: 'Không có học liệu được phép truy xuất trong phạm vi hiện tại.' }
    }
    if (result.message === 'QUERY_NO_KEYWORDS') {
        return { title: 'Cần bổ sung nội dung tìm kiếm', message: 'Hãy nhập từ khóa hoặc câu hỏi cụ thể hơn.' }
    }
    const partial = Object.values(result.branch_status || {}).some(value =>
        !['READY', 'OK', 'NOT_REQUESTED', 'DISABLED', 'NO_ACCESSIBLE_CONTENT', 'NO_READY_INDEX'].includes(value))
    if (partial || result.readiness?.reindex_required || result.message === 'NO_READY_INDEX') {
        return { title: 'Tìm kiếm chưa đầy đủ', message: 'Một phần chỉ mục hoặc dịch vụ tìm kiếm chưa sẵn sàng. Chưa thể kết luận kho không có học liệu phù hợp; hãy thử lại sau hoặc đổi cách tìm.' }
    }
    return { title: 'Chưa tìm thấy học liệu', message: 'Không tìm thấy học liệu phù hợp. Hãy thử từ khóa khác hoặc giảm bộ lọc.' }
}

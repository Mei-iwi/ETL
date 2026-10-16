import type { DatasetInput } from '../../types/evaluation'

export type DraftQuery = { query_id: string; query: string; labels: string }
export const blankQuery = (count: number): DraftQuery => ({ query_id: `q${count}`, query: '', labels: '' })
export function datasetInput(name: string, description: string, queries: DraftQuery[], confirmed: boolean): DatasetInput {
    if (!confirmed) throw new Error('Cần xác nhận các nhãn đã được con người đánh giá.')
    if (!name.trim() || queries.length < 1 || queries.length > 20) throw new Error('Nhập tên và từ 1–20 truy vấn.')
    const queryIds = new Set<string>()
    return { name: name.trim(), description, relevance_unit: 'resource', ground_truth_confirmed: true,
        queries: queries.map(row => {
            if (!/^[A-Za-z0-9_-]{1,50}$/.test(row.query_id) || queryIds.has(row.query_id)) throw new Error('Mã truy vấn không hợp lệ hoặc bị trùng.')
            queryIds.add(row.query_id)
            const query = row.query.normalize('NFC').trim()
            if (!query || query.length > 300) throw new Error('Mỗi truy vấn cần từ 1–300 ký tự.')
            const labels = row.labels.split(/[\n,]+/).map(value => value.trim()).filter(Boolean)
            const ids = new Set<string>()
            if (!labels.length || labels.length > 100) throw new Error('Mỗi truy vấn cần từ 1–100 nhãn.')
            const relevance = labels.map(value => {
                const match = /^([A-Za-z0-9_-]{1,30}):([0-3])$/.exec(value)
                if (!match || ids.has(match[1])) throw new Error(`Nhãn ${row.query_id}: dùng resource_id:grade (0–3), không trùng ID, không để trống grade.`)
                ids.add(match[1])
                return { item_id: match[1], grade: Number(match[2]) }
            })
            return { query_id: row.query_id, query, relevance }
        }) }
}

export function importDraft(value: unknown): { name: string; description: string; queries: DraftQuery[] } {
    if (!value || typeof value !== 'object') throw new Error('File JSON không hợp lệ.')
    const body = value as Record<string, unknown>
    const rows = Array.isArray(body.rows) ? body.rows : null
    if (rows) {
        const grouped = new Map<string, DraftQuery>()
        for (const item of rows) {
            if (!item || typeof item !== 'object') throw new Error('Dòng nhãn không hợp lệ.')
            const row = item as Record<string, unknown>
            if (typeof row.query_id !== 'string' || typeof row.query_text !== 'string' || typeof row.resource_id !== 'string') throw new Error('Thiếu query_id/query_text/resource_id.')
            const entry = grouped.get(row.query_id) || { query_id: row.query_id, query: row.query_text, labels: '' }
            if (entry.query !== row.query_text) throw new Error('Query ID trùng nhưng nội dung khác nhau.')
            entry.labels += `${entry.labels ? '\n' : ''}${row.resource_id}:${row.relevance ?? ''}`
            grouped.set(row.query_id, entry)
        }
        if (grouped.size < 1 || grouped.size > 20) throw new Error('File chỉ hỗ trợ từ 1–20 truy vấn.')
        return { name: typeof body.name === 'string' ? body.name : '', description: 'Nhập từ file gán nhãn thủ công; cần xác nhận trước khi lưu.', queries: [...grouped.values()] }
    }
    if (!Array.isArray(body.queries) || body.queries.length < 1 || body.queries.length > 20) throw new Error('File chỉ hỗ trợ từ 1–20 truy vấn.')
    return { name: typeof body.name === 'string' ? body.name : '', description: typeof body.description === 'string' ? body.description : '',
        queries: body.queries.map(item => {
            if (!item || typeof item !== 'object' || typeof item.query_id !== 'string' || typeof item.query !== 'string' || !Array.isArray(item.relevance)) throw new Error('Cấu trúc truy vấn không hợp lệ.')
            return { query_id: item.query_id, query: item.query,
                labels: item.relevance.map((label: Record<string, unknown>) => `${label.item_id}:${label.grade ?? ''}`).join('\n') }
        }) }
}

export const number = (value: number | null | undefined) => value == null ? 'n/a' : value.toFixed(4)
export const warning = (code: string) => ({
    UNJUDGED_ITEMS_TREATED_AS_GRADE_0: 'Học liệu chưa gán nhãn được tính grade 0.',
    LOCAL_DEMO_NOT_PRODUCTION_AUTH: 'Chỉ sử dụng trong Dev local; chưa có xác thực Production.',
    SMALL_DATASET_NO_GENERAL_CONCLUSION: 'Dưới 10 truy vấn: chưa đủ để kết luận tổng quát.',
    QUERIES_WITHOUT_POSITIVE_QRELS_EXCLUDED_FROM_RECALL_MRR_NDCG: 'Có truy vấn không có nhãn dương; Recall/MRR/nDCG loại khỏi trung bình khi không xác định.',
    EVALUATION_CORPUS_CHANGED: 'Corpus/chỉ mục/policy đã thay đổi; không dùng run này để so sánh.',
    EVALUATION_TIMEOUT: 'Đã hết thời gian thực nghiệm. Hãy giảm số truy vấn.',
}[code] || code)

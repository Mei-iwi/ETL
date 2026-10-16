import type { DatasetInput } from '../../types/evaluation'

export type DraftCandidate = { id: string; title: string; description: string; grade: number | null }
export type DraftQuery = { query_id: string; query: string; labels: string; candidates?: DraftCandidate[] }
export function setDraftGrade(row: DraftQuery, id: string, grade: number | null): DraftQuery {
    const candidates = row.candidates?.map(item => item.id === id ? { ...item, grade } : item)
    return { ...row, candidates, labels: candidates?.map(item => `${item.id}:${item.grade ?? ''}`).join('\n') ?? row.labels }
}

function candidate(id: unknown, grade: unknown, title: unknown = '', description: unknown = ''): DraftCandidate {
    if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,30}$/.test(id)) throw new Error('Resource ID trong JSON không hợp lệ.')
    if (grade != null && (typeof grade !== 'number' || !Number.isInteger(grade) || grade < 0 || grade > 3)) throw new Error(`Học liệu ${id}: điểm phải là 0–3 hoặc null nếu chưa chấm.`)
    return { id, grade: grade ?? null, title: typeof title === 'string' ? title : '', description: typeof description === 'string' ? description : '' }
}

function withCandidates(row: DraftQuery, candidates: DraftCandidate[]): DraftQuery {
    if (!candidates.length || candidates.length > 100 || new Set(candidates.map(item => item.id)).size !== candidates.length) throw new Error(`Truy vấn ${row.query_id}: cần 1–100 học liệu, không trùng ID.`)
    return { ...row, candidates, labels: candidates.map(item => `${item.id}:${item.grade ?? ''}`).join('\n') }
}
export const blankQuery = (count: number, existing: DraftQuery[] = []): DraftQuery => {
    while (existing.some(row => row.query_id === `q${count}`)) count++
    return { query_id: `q${count}`, query: '', labels: '' }
}
export function datasetInput(name: string, description: string, queries: DraftQuery[], confirmed = false): DatasetInput {
    if (!name.trim() || queries.length < 1 || queries.length > 20) throw new Error('Nhập tên và từ 1–20 truy vấn.')
    const queryIds = new Set<string>()
    return { name: name.trim(), description, relevance_unit: 'resource', ground_truth_confirmed: confirmed,
        queries: queries.map(row => {
            if (!/^[A-Za-z0-9_-]{1,50}$/.test(row.query_id) || queryIds.has(row.query_id)) throw new Error('Mã truy vấn không hợp lệ hoặc bị trùng.')
            queryIds.add(row.query_id)
            const query = row.query.normalize('NFC').trim()
            if (!query || query.length > 300) throw new Error('Mỗi truy vấn cần từ 1–300 ký tự.')
            if (row.candidates?.some(item => item.grade === null)) throw new Error(`Truy vấn ${row.query_id}: còn ${row.candidates.filter(item => item.grade === null).length} học liệu chưa chấm. Chọn điểm 0–3 cho từng học liệu.`)
            const labels = row.labels.split(/[\n,]+/).map(value => value.trim()).filter(Boolean)
            const ids = new Set<string>()
            if (!labels.length || labels.length > 100) throw new Error('Mỗi truy vấn cần từ 1–100 nhãn.')
            const relevance = labels.map(value => {
                const match = /^([A-Za-z0-9_-]{1,30})\s*:\s*([0-3])$/.exec(value)
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
            const entry = grouped.get(row.query_id) || { query_id: row.query_id, query: row.query_text, labels: '', candidates: [] }
            if (entry.query !== row.query_text) throw new Error('Query ID trùng nhưng nội dung khác nhau.')
            grouped.set(row.query_id, withCandidates(entry, [...(entry.candidates ?? []), candidate(row.resource_id, row.relevance, row.resource_title, row.candidate_description)]))
        }
        if (grouped.size < 1 || grouped.size > 20) throw new Error('File chỉ hỗ trợ từ 1–20 truy vấn.')
        return { name: typeof body.name === 'string' ? body.name : '', description: typeof body.description === 'string' ? body.description : '', queries: [...grouped.values()] }
    }
    if (!Array.isArray(body.queries) || body.queries.length < 1 || body.queries.length > 20) throw new Error('File chỉ hỗ trợ từ 1–20 truy vấn.')
    return { name: typeof body.name === 'string' ? body.name : '', description: typeof body.description === 'string' ? body.description : '',
        queries: body.queries.map(item => {
            if (!item || typeof item !== 'object' || typeof item.query_id !== 'string' || typeof item.query !== 'string' || !Array.isArray(item.relevance)) throw new Error('Cấu trúc truy vấn không hợp lệ.')
            return withCandidates({ query_id: item.query_id, query: item.query, labels: '' }, item.relevance.map((label: unknown) => {
                if (!label || typeof label !== 'object') throw new Error('Nhãn JSON không hợp lệ.')
                const value = label as Record<string, unknown>
                return candidate(value.item_id, value.grade, value.resource_title, value.candidate_description)
            }))
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
    EVALUATION_CANCELLED: 'Kết nối đã đóng trước khi hoàn tất thực nghiệm.',
    LABELS_NOT_HUMAN_CONFIRMED: 'Bộ nhãn chưa xác nhận Ground Truth; chỉ dùng kiểm thử.',
}[code] || code)

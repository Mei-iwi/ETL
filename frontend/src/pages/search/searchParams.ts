import type { SearchMethod, SearchRequest } from '../../types/search'

export const SEARCH_QUERY_MAX_LENGTH = 2000

export const methods: { value: SearchMethod; label: string; }[] = [
    { value: 'hybrid', label: 'Tìm kết hợp'},
    { value: 'bow', label: 'Theo từ khóa'},
    { value: 'vector', label: 'Theo ý nghĩa' },
]

export function readSearch(params: URLSearchParams): SearchRequest {
    const method = params.get('method')
    const number = (key: string, fallback: number, max: number) => {
        const value = Number(params.get(key))
        return Number.isInteger(value) && value >= 1 && value <= max ? value : fallback
    }
    return {
        query: (params.get('q') || '').normalize('NFC').trim(),
        method: methods.some(item => item.value === method) ? method as SearchMethod : 'hybrid',
        filters: Object.fromEntries(['domain', 'subject_id', 'grade_level_id', 'resource_type_code', 'resource_format', 'resource_id', 'resource_version_id']
            .map(key => [key, params.get(key) || null])),
        page: number('page', 1, 10001), size: 5,
    }
}

export function writeSearch(request: SearchRequest) {
    const params = new URLSearchParams({ q: request.query.normalize('NFC').trim(), method: request.method,
        page: String(request.page), size: String(request.size) })
    for (const [key, value] of Object.entries(request.filters)) if (value) params.set(key, value)
    return params
}

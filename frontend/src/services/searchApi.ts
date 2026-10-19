import type { ChunkContext, SearchRequest, SearchResponse } from '../types/search'

export class SearchApiError extends Error {
    status: number
    code: string

    constructor(status: number, code: string, message: string) {
        super(message)
        this.status = status
        this.code = code
    }
}

const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const textFields = (value: Record<string, unknown>, keys: string[]) => keys.every(key => typeof value[key] === 'string')
const integer = (value: unknown, min = 0): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= min
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const nullableText = (value: unknown) => value === null || typeof value === 'string'
const pageNumber = (value: unknown) => value === null || integer(value)
const scope = (value: Record<string, unknown>) => value.access_scope === undefined || value.access_scope === 'public' || value.access_scope === 'local_demo'

function validItem(value: unknown) {
    return record(value) && textFields(value, ['resource_id', 'resource_title', 'resource_version_id', 'resource_type_code',
        'bag_id', 'unit_type', 'snippet', 'content_unit_id', 'context_id', 'context_url'])
        && integer(value.version_number, 1) && nullableText(value.provider_name) && nullableText(value.bag_title)
        && ['structured', 'fallback'].includes(value.representation_mode as string)
        && Array.isArray(value.matched_keywords) && value.matched_keywords.every(word => typeof word === 'string')
        && integer(value.matched_bag_count, 1) && finite(value.score) && record(value.score_components)
        && Object.values(value.score_components).every(finite) && pageNumber(value.page_from) && pageNumber(value.page_to)
}

function validSearch(value: unknown, request: SearchRequest): value is SearchResponse {
    return record(value) && !('detail' in value) && !('error' in value) && scope(value)
        && value.query === request.query.normalize('NFC').trim() && value.method === request.method
        && value.page === request.page && value.size === request.size && integer(value.page, 1) && integer(value.size, 1)
        && integer(value.total) && integer(value.total_pages) && value.total_pages === Math.ceil(value.total / value.size)
        && typeof value.ranking_algorithm === 'string' && value.representation_unit === 'content_bag' && integer(value.ready_bag_count)
        && ['OK', 'NO_READY_INDEX', 'NO_ACCESSIBLE_CONTENT', 'QUERY_NO_KEYWORDS', 'PUBLIC_ACCESS_UNCONFIRMED'].includes(value.message as string)
        && Array.isArray(value.items) && value.items.length <= value.size && value.items.length <= value.total && value.items.every(validItem)
}

function validContext(value: unknown, id: string, neighbors: number): value is ChunkContext {
    return record(value) && !('detail' in value) && !('error' in value) && scope(value)
        && textFields(value, ['resource_id', 'resource_title', 'resource_version_id', 'content_unit_id', 'bag_id'])
        && integer(value.version_number, 1) && value.context_id === id && value.neighbors === neighbors
        && Array.isArray(value.heading_path) && value.heading_path.every(heading => record(heading)
            && textFields(heading, ['content_unit_id', 'unit_type']) && nullableText(heading.title))
        && Array.isArray(value.chunks) && value.chunks.filter(chunk => record(chunk) && chunk.selected === true).length === 1
        && value.chunks.every(chunk => record(chunk) && textFields(chunk, ['id', 'content_unit_id', 'text'])
            && integer(chunk.unit_chunk_sequence, 1) && integer(chunk.char_start) && integer(chunk.char_end)
            && chunk.char_end >= chunk.char_start && pageNumber(chunk.page_from) && pageNumber(chunk.page_to) && typeof chunk.selected === 'boolean')
}

const messages: Record<string, string> = {
    SEARCH_RUNTIME_BUSY: 'Đang xử lý tìm kiếm khác. Vui lòng thử lại sau.',
    SEARCH_ACCESS_RESTRICTED: 'Truy cập tìm kiếm nội bộ bị hạn chế.',
    SEARCH_LIMIT_EXCEEDED: 'Kho dữ liệu vượt giới hạn. Hãy thu hẹp bộ lọc.',
    SEARCH_TIMEOUT: 'Tìm kiếm quá thời gian. Vui lòng thử lại hoặc chọn BoW.',
    SEARCH_MODEL_WARMUP_TIMEOUT: 'Nạp model quá thời gian. Vui lòng thử lại hoặc chọn BoW.',
    BLOCKED_EMBEDDING_RUNTIME: 'Model chưa sẵn sàng. Có thể chọn BoW.',
    INVALID_EMBEDDING: 'Model embedding chưa sẵn sàng. Có thể chọn BoW.',
    SEARCH_INDEX_STALE: 'Chỉ mục vừa thay đổi. Vui lòng tìm lại.',
    CONTEXT_STALE: 'Nội dung đã thay đổi. Vui lòng tìm lại.',
}
const invalidResponse = () => new SearchApiError(502, 'SEARCH_INVALID_RESPONSE', 'Dữ liệu tìm kiếm không hợp lệ. Vui lòng thử lại.')

async function request<T>(path: string, validate: (value: unknown) => value is T, signal?: AbortSignal, payload?: SearchRequest): Promise<T> {
    // Server deadlines are cooperative; abort stops waiting, not running inference.
    const combined = AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(payload ? 90000 : 20000)])
    try {
        const response = await fetch(path, {
            method: payload ? 'POST' : 'GET', signal: combined,
            cache: 'no-store',
            headers: { Accept: 'application/json', ...(payload ? { 'Content-Type': 'application/json' } : {}) },
            ...(payload ? { body: JSON.stringify(payload) } : {}),
        })
        let body: unknown
        try { body = await response.json() } catch (error) {
            if (combined.aborted) throw error
            if (response.ok) {
                if (error instanceof SyntaxError) throw invalidResponse()
                throw error  // Failed body transport is not a JSON validation error.
            }
            body = null
        }
        combined.throwIfAborted()
        if (!response.ok) {
            const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null
            const code = detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string'
                ? detail.code : 'SEARCH_UNAVAILABLE'
            // Only local, allowlisted text is displayed; never show adapter/proxy details.
            const message = Object.hasOwn(messages, code) ? messages[code] : response.status === 401 ? 'Yêu cầu xác thực để truy cập nội dung này.'
                : response.status === 403 ? 'Truy cập tìm kiếm bị hạn chế.'
                : response.status === 404 ? 'Không tìm thấy nội dung hoặc không có quyền truy cập.'
                : response.status === 409 ? 'Nội dung vừa thay đổi. Vui lòng tìm lại.'
                : response.status === 422 ? 'Truy vấn hoặc bộ lọc chưa hợp lệ.'
                : `Không thể tải dữ liệu tìm kiếm (HTTP ${response.status}).`
            throw new SearchApiError(response.status, code, message)
        }
        if (!validate(body)) throw invalidResponse()
        return body
    } catch (error) {
        if (signal?.aborted) throw new DOMException('Đã ngừng chờ tìm kiếm.', 'AbortError')
        if (combined.aborted || (error instanceof DOMException && error.name === 'TimeoutError')) {
            throw new SearchApiError(0, 'SEARCH_TIMEOUT', messages.SEARCH_TIMEOUT)
        }
        if (error instanceof SearchApiError) throw error
        throw new SearchApiError(0, 'SEARCH_NETWORK_ERROR', 'Không kết nối được máy chủ. Vui lòng thử lại.')
    }
}

export function searchMaterials(payload: SearchRequest, signal?: AbortSignal) {
    return request('/api/v1/search', (value): value is SearchResponse => validSearch(value, payload), signal, payload)
}

export function getChunkContext(id: string, signal?: AbortSignal, neighbors = 1) {
    return request(`/api/v1/chunks/${encodeURIComponent(id)}/context?neighbors=${neighbors}`,
        (value): value is ChunkContext => validContext(value, id, neighbors), signal)
}

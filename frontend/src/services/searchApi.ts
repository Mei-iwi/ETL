import type { ChunkContext, SearchRequest, SearchResponse } from '../types/search'

export class SearchApiError extends Error {
    status: number
    code: string

    constructor(status: number, code: string, message: string) {
        super(message)
        this.name = 'SearchApiError'
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
const scope = (value: Record<string, unknown>) => value.access_scope === undefined || value.access_scope === 'public' || value.access_scope === 'local_demo' || value.access_scope === 'authenticated_admin' || value.access_scope === 'authenticated_user'

function validLexicalEvidence(value: unknown) {
    return record(value) && textFields(value, ['query_phrase', 'matched_phrase', 'relation', 'field'])
        && nullableText(value.concept_id) && finite(value.weight) && value.weight >= 0
        && ['title', 'content'].includes(value.field as string)
        && Array.isArray(value.sources) && value.sources.every(source => record(source) && textFields(source, ['name', 'identifier']))
}

function validItem(value: unknown) {
    return record(value) && textFields(value, ['resource_id', 'resource_title', 'resource_version_id', 'resource_type_code',
        'snippet'])
        && ['bag_id', 'unit_type', 'content_unit_id', 'context_id', 'context_url'].every(key => nullableText(value[key]))
        && (value.match_origin === undefined || ['METADATA', 'CONTENT', 'BOTH'].includes(value.match_origin as string))
        && (value.resource_format === undefined || typeof value.resource_format === 'string')
        && (value.evidence_kind === undefined || ['METADATA_FIELD', 'CONTENT_CHUNK'].includes(value.evidence_kind as string))
        && (value.evidence_text === undefined || typeof value.evidence_text === 'string')
        && (value.matched_fields === undefined || (Array.isArray(value.matched_fields) && value.matched_fields.every(field => typeof field === 'string')))
        && (value.representation_mode !== 'metadata' || (value.match_origin === 'METADATA' && value.context_id === null && value.context_url === null && value.content_unit_id === null && value.bag_id === null && value.can_view_file === false))
        && integer(value.version_number, 1) && nullableText(value.provider_name) && nullableText(value.bag_title)
        && (value.can_view_file === undefined || typeof value.can_view_file === 'boolean')
        && ['structured', 'fallback', 'metadata'].includes(value.representation_mode as string)
        && (value.context_evidence == null || ['lexical', 'semantic', 'bag'].includes(value.context_evidence as string))
        && (value.meaning_variants === undefined || (Array.isArray(value.meaning_variants) && value.meaning_variants.every(item => typeof item === 'string')))
        && (value.lexical_evidence === undefined || (Array.isArray(value.lexical_evidence) && value.lexical_evidence.length <= 64 && value.lexical_evidence.every(validLexicalEvidence)))
        && Array.isArray(value.matched_keywords) && value.matched_keywords.every(word => typeof word === 'string')
        && integer(value.matched_bag_count, value.representation_mode === 'metadata' ? 0 : 1) && finite(value.score) && record(value.score_components)
        && Object.values(value.score_components).every(finite) && pageNumber(value.page_from) && pageNumber(value.page_to)
}

function validSearch(value: unknown, request: SearchRequest): value is SearchResponse {
    return record(value) && !('detail' in value) && !('error' in value) && scope(value)
        && (value.branch_status === undefined || (record(value.branch_status) && Object.values(value.branch_status).every(item => typeof item === 'string')))
        && (value.readiness == null || (record(value.readiness)
            && integer(value.readiness.lexical_ready_bags) && integer(value.readiness.dense_ready_bags)
            && integer(value.readiness.eligible_versions) && integer(value.readiness.ready_versions)
            && value.readiness.ready_versions <= value.readiness.eligible_versions
            && typeof value.readiness.reindex_required === 'boolean'))
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
    SEARCH_NETWORK_ERROR: 'Không kết nối được máy chủ. Vui lòng thử lại.',
    SEARCH_INVALID_RESPONSE: 'Dữ liệu tìm kiếm không hợp lệ. Vui lòng thử lại.',
    BLOCKED_LEXICAL_ANALYZER: 'Bộ phân tích chỉ mục chưa sẵn sàng. Vui lòng liên hệ quản trị viên.',
    SEARCH_MODEL_LOADING: 'Đang chuẩn bị tìm kiếm. Có thể chọn BoW.',
    SEARCH_RUNTIME_BUSY: 'Đang xử lý tìm kiếm khác. Vui lòng thử lại sau.',
    SEARCH_ACCESS_RESTRICTED: 'Truy cập tìm kiếm nội bộ bị hạn chế.',
    SEARCH_LIMIT_EXCEEDED: 'Kho dữ liệu vượt giới hạn. Hãy thu hẹp bộ lọc.',
    SEARCH_TIMEOUT: 'Tìm kiếm quá thời gian. Vui lòng thử lại.',
    SEARCH_MODEL_WARMUP_TIMEOUT: 'Chuẩn bị tìm kiếm quá thời gian. Vui lòng thử lại hoặc chọn BoW.',
    BLOCKED_EMBEDDING_RUNTIME: 'Chưa thể chuẩn bị tìm kiếm. Có thể chọn BoW.',
    INVALID_EMBEDDING: 'Tìm kiếm theo ngữ nghĩa chưa sẵn sàng. Có thể chọn BoW.',
    BLOCKED_INDEX_SCHEMA: 'Chỉ mục tìm kiếm chưa tương thích với cấu hình hiện tại. Vui lòng liên hệ quản trị viên để kiểm tra và cập nhật chỉ mục.',
    SEARCH_SCHEMA_UNAVAILABLE: 'Chưa kết nối được dữ liệu chỉ mục tìm kiếm. Vui lòng thử lại sau.',
    SEARCH_INDEX_STALE: 'Chỉ mục vừa thay đổi. Vui lòng tìm lại.',
    CONTEXT_STALE: 'Nội dung đã thay đổi. Vui lòng tìm lại.',
}

function safeErrorMessage(status: number, code: string) {
    return Object.hasOwn(messages, code) ? messages[code] : status === 401 ? 'Yêu cầu xác thực để truy cập nội dung này.'
        : status === 403 ? 'Truy cập tìm kiếm bị hạn chế.'
            : status === 404 ? 'Không tìm thấy nội dung hoặc không có quyền truy cập.'
                : status === 409 ? 'Nội dung vừa thay đổi. Vui lòng tìm lại.'
                    : status === 422 ? 'Truy vấn hoặc bộ lọc chưa hợp lệ.'
                        : `Không thể tải dữ liệu tìm kiếm (HTTP ${status}).`
}

export function searchFailureMessage(error: unknown) {
    // HMR may replace the constructor while a request still holds an older
    // Error instance. Class identity must not erase its typed failure reason.
    // Never display arbitrary error.message or backend/proxy text.
    if (record(error) && integer(error.status) && error.status <= 599 && typeof error.code === 'string')
        return safeErrorMessage(error.status, error.code)
    if (error instanceof DOMException && error.name === 'AbortError')
        return 'Phiên truy cập đã thay đổi. Vui lòng tìm lại.'
    return 'Tìm kiếm thất bại. Vui lòng thử lại.'
}

export type SearchModelStatus = { state: 'UNINITIALIZED' | 'LOADING' | 'READY' | 'FAILED' | 'STOPPED'; ready: boolean }

export function getSearchModelStatus(signal?: AbortSignal): Promise<SearchModelStatus> {
    return request('/api/v1/search/model-status', validModelStatus, signal)
}

const validModelStatus = (value: unknown): value is SearchModelStatus => record(value)
    && ['UNINITIALIZED', 'LOADING', 'READY', 'FAILED', 'STOPPED'].includes(value.state as string)
    && typeof value.ready === 'boolean' && value.ready === (value.state === 'READY')

export function retrySearchModel(signal?: AbortSignal): Promise<SearchModelStatus> {
    return request('/api/v1/search/model-retry', validModelStatus, signal, undefined, 'POST')
}
const invalidResponse = () => new SearchApiError(502, 'SEARCH_INVALID_RESPONSE', 'Dữ liệu tìm kiếm không hợp lệ. Vui lòng thử lại.')

async function request<T>(path: string, validate: (value: unknown) => value is T, signal?: AbortSignal, payload?: SearchRequest, method: 'GET' | 'POST' = 'GET'): Promise<T> {
    // Server deadlines are cooperative; abort stops waiting, not running inference.
    const combined = AbortSignal.any([...(signal ? [signal] : []),
        AbortSignal.timeout(payload && payload.method !== 'bow' ? 90000 : 20000)])
    let abortWait: () => void
    const aborted = new Promise<never>((_, reject) => {
        abortWait = () => reject(combined.reason)
        combined.addEventListener('abort', abortWait, { once: true })
    })
    try {
        combined.throwIfAborted()
        const response = await Promise.race([fetch(path, {
            method: payload ? 'POST' : method, signal: combined,
            cache: 'no-store',
            headers: { Accept: 'application/json', ...(payload ? { 'Content-Type': 'application/json' } : {}) },
            ...(payload ? { body: JSON.stringify(payload) } : {}),
        }), aborted])
        let body: unknown
        try { body = await Promise.race([response.json(), aborted]) } catch (error) {
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
            const message = safeErrorMessage(response.status, code)
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
    } finally {
        combined.removeEventListener('abort', abortWait!)
    }
}

export function searchMaterials(payload: SearchRequest, signal?: AbortSignal) {
    return request('/api/v1/search', (value): value is SearchResponse => validSearch(value, payload), signal, payload)
}

export function searchSessionPage(payload: SearchRequest & { session_id: string }, signal?: AbortSignal) {
    return request('/api/v1/search/page', (value): value is SearchResponse => validSearch(value, payload), signal, payload)
}

export function getChunkContext(id: string, signal?: AbortSignal, neighbors = 1) {
    return request(`/api/v1/chunks/${encodeURIComponent(id)}/context?neighbors=${neighbors}`,
        (value): value is ChunkContext => validContext(value, id, neighbors), signal)
}

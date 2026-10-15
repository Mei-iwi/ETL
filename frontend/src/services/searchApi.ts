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

async function request<T>(path: string, signal?: AbortSignal, payload?: SearchRequest): Promise<T> {
    // Search may load the local model once (60s) before its query budget (15s).
    const combined = AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(payload ? 90000 : 20000)])
    const response = await fetch(path, {
        method: payload ? 'POST' : 'GET', signal: combined,
        headers: { Accept: 'application/json', ...(payload ? { 'Content-Type': 'application/json' } : {}) },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
    })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
        const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null
        const code = detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string'
            ? detail.code : 'SEARCH_UNAVAILABLE'
        const message = detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string'
            ? detail.message : response.status === 401 ? 'Yêu cầu xác thực để truy cập nội dung này.'
                : response.status === 403 ? 'Truy cập tìm kiếm bị hạn chế.'
                : `Không thể tải dữ liệu tìm kiếm (HTTP ${response.status}).`
        throw new SearchApiError(response.status, code, message)
    }
    return body as T
}

export function searchMaterials(payload: SearchRequest, signal?: AbortSignal) {
    return request<SearchResponse>('/api/v1/search', signal, payload)
}

export function getChunkContext(id: string, signal?: AbortSignal, neighbors = 1) {
    return request<ChunkContext>(`/api/v1/chunks/${encodeURIComponent(id)}/context?neighbors=${neighbors}`, signal)
}

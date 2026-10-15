import type { ChunksResult, IndexRequestResult, IndexStatusResult, ResourceOption, VersionOption } from '../types/indexing'

export class IndexingApiError extends Error {
    status: number
    code: string

    constructor(status: number, code: string, message: string) {
        super(message)
        this.status = status
        this.code = code
    }
}

async function request<T>(path: string, signal?: AbortSignal, method = 'GET'): Promise<T> {
    const combined = AbortSignal.any([...(signal ? [signal] : []), AbortSignal.timeout(15000)])
    const response = await fetch(path, { method, signal: combined, headers: { Accept: 'application/json' } })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
        const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null
        const code = detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string'
            ? detail.code : 'INDEX_UNAVAILABLE'
        const message = detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string'
            ? detail.message : response.status === 403 ? 'Chưa bật quyền quản trị giao diện local.'
                : 'Không thể tải dữ liệu lập chỉ mục. Vui lòng thử lại.'
        throw new IndexingApiError(response.status, code, message)
    }
    return body as T
}

export function startIndex(versionId: string, signal?: AbortSignal) {
    return request<IndexRequestResult>(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/index`, signal, 'POST')
}

export function getIndexResources(query: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ page: '1', size: '100', q: query })
    return request<{ items: ResourceOption[]; pagination: { total: number } }>(`/api/v1/resources?${params}`, signal)
}

export function getIndexVersions(resourceId: string, signal?: AbortSignal) {
    return request<VersionOption[]>(`/api/v1/resources/${encodeURIComponent(resourceId)}/versions`, signal)
}

export function getChunks(versionId: string, offset = 0, signal?: AbortSignal, contentUnitId = '') {
    const params = new URLSearchParams({ offset: String(offset), limit: '10' })
    if (contentUnitId) params.set('content_unit_id', contentUnitId)
    return request<ChunksResult>(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/chunks?${params}`, signal)
}

export function getIndexStatus(versionId: string, signal?: AbortSignal) {
    return request<IndexStatusResult>(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/index-status`, signal)
}

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

export function getIndexStatus(versionId: string, signal?: AbortSignal, includeBags = true) {
    return request<IndexStatusResult>(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/index-status${includeBags ? '' : '?include_bags=false'}`, signal)
}

export async function getBagPage(versionId: string, publishedRunId: string, offset = 0,
    signal?: AbortSignal, revision?: string) {
    const params = new URLSearchParams({ bag_offset: String(offset), bag_limit: '10', expected_run_id: publishedRunId })
    if (revision) params.set('bag_revision', revision)
    const result = await request<IndexStatusResult>(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/index-status?${params}`, signal)
    if (result.resource_version_id !== versionId || result.published_run_id !== publishedRunId || result.index_status !== 'READY'
        || result.bag_pagination?.offset !== offset || result.bag_pagination.limit !== 10
        || !result.bag_revision || !/^[a-f0-9]{64}$/.test(result.bag_revision) || (revision && revision !== result.bag_revision)
        || !Number.isInteger(result.bag_pagination.total) || result.bag_pagination.total < 0
        || result.bag_count !== result.bag_pagination.total
        || !Array.isArray(result.bags) || result.bags.length > 10) throw new IndexingApiError(409, 'INDEX_PAGE_CHANGED', 'Chỉ mục đã thay đổi. Tải lại trạng thái trước khi xem túi từ.')
    return result
}

export function reindex(versionId: string, signal?: AbortSignal) {
    return request<IndexRequestResult>(`/api/v1/resource-versions/${encodeURIComponent(versionId)}/reindex`, signal, 'POST')
}

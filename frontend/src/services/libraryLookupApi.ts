export type ResourceTypeOption = {
    code: string
    name: string
}

export type ProviderOption = {
    id: string
    name: string
    provider_type: string
}

export type GradeLevelOption = {
    id: string
    grade_code: string
    grade_name_vi: string
}

export type SubjectOption = {
    id: string
    name: string
    slug: string
    grade_level_id: string | null
}

export type CatalogMetadata = {
    identity_overlaps?: number
    source_status: string
    completeness: string
}
export type CatalogItems<T> = T[] & { catalog?: CatalogMetadata }

export function catalogMessage(metadata?: CatalogMetadata): string {
    if (metadata?.completeness === 'ALL_SOURCE_PLUS_LOCAL') {
        if (metadata.identity_overlaps) return 'Đầy đủ từ SOURCE, kèm LOCAL; identity trùng dùng thông tin SOURCE.'
        return metadata.source_status === 'SOURCE_EMPTY'
            ? 'SOURCE hiện không có danh mục active; đã cộng danh mục LOCAL hợp lệ.'
            : 'Đầy đủ từ SOURCE, kèm danh mục LOCAL hợp lệ.'
    }
    if (metadata?.completeness === 'MASTER_MANAGED') return 'Danh mục active được quản lý trong Master.'
    return 'Dữ liệu dự phòng từ Master; có thể thiếu danh mục. Chưa xác minh được SOURCE.'
}

export type CatalogResponse<T> = {
    catalog?: CatalogMetadata
    items: T[]
    pagination: {
        page: number
        size: number
        total: number
        total_pages: number
    }
}

async function getErrorMessage(response: Response) {
    const body: unknown = await response.json().catch(() => null)
    if (body && typeof body === 'object' && 'detail' in body) {
        if (typeof body.detail === 'string') return body.detail
        if (
            body.detail && typeof body.detail === 'object' &&
            'message' in body.detail && typeof body.detail.message === 'string'
        ) {
            return body.detail.message
        }
    }
    return `Không tải được danh mục (HTTP ${response.status})`
}

export async function getCatalogPage<T>(
    path: string,
    page: number,
    signal?: AbortSignal,
    options: { size?: number; query?: string } = {},
): Promise<CatalogResponse<T>> {
    const params = new URLSearchParams({
        page: String(page),
        size: String(options.size ?? 100),
        ...(options.query?.trim() ? { q: options.query.trim() } : {}),
    })
    const response = await fetch(`${path}?${params.toString()}`, {
        signal,
        headers: { Accept: 'application/json' },
    })
    if (!response.ok) throw new Error(await getErrorMessage(response))
    return response.json() as Promise<CatalogResponse<T>>
}

async function getAllCatalogItems<T>(path: string, signal?: AbortSignal) {
    const firstPage = await getCatalogPage<T>(path, 1, signal)
    if (!Number.isInteger(firstPage.pagination.total_pages) || firstPage.pagination.total_pages < 0 || firstPage.pagination.total_pages > 1000) {
        throw new Error('Danh mục vượt giới hạn tải. Hãy dùng phân trang.')
    }
    const items: CatalogItems<T> = Object.assign([...firstPage.items], { catalog: firstPage.catalog })

    for (let page = 2; page <= firstPage.pagination.total_pages; page += 1) {
        const nextPage = await getCatalogPage<T>(path, page, signal)
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        if (JSON.stringify(nextPage.catalog) !== JSON.stringify(firstPage.catalog)) {
            throw new Error('Trạng thái SOURCE đã thay đổi khi tải danh mục. Hãy tải lại.')
        }
        items.push(...nextPage.items)
    }

    // Independent requests are eventually consistent. Deduplicate stable API identities.
    const seen = new Set<string>()
    return Object.assign(items.filter(item => {
        const row = item as Record<string, unknown>
        const key = String(row.id ?? row.code)
        if (seen.has(key)) return false
        seen.add(key); return true
    }), { catalog: items.catalog })
}

export function getResourceTypes(signal?: AbortSignal) {
    return getAllCatalogItems<ResourceTypeOption>('/api/v1/resource-types', signal)
}

export function getProviders(signal?: AbortSignal) {
    return getAllCatalogItems<ProviderOption>('/api/v1/providers', signal)
}

export function getGradeLevels(signal?: AbortSignal) {
    return getAllCatalogItems<GradeLevelOption>('/api/v1/grade-levels', signal)
}

export function getSubjects(signal?: AbortSignal) {
    return getAllCatalogItems<SubjectOption>('/api/v1/subjects', signal)
}

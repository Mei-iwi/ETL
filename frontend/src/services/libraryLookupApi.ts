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

type CatalogResponse<T> = {
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

async function getCatalogPage<T>(
    path: string,
    page: number,
    signal?: AbortSignal,
): Promise<CatalogResponse<T>> {
    const params = new URLSearchParams({
        page: String(page),
        size: '100',
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
    const items = [...firstPage.items]

    for (let page = 2; page <= firstPage.pagination.total_pages; page += 1) {
        const nextPage = await getCatalogPage<T>(path, page, signal)
        items.push(...nextPage.items)
    }

    return items
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

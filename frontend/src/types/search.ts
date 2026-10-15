export type SearchMethod = 'bow' | 'vector' | 'hybrid'
export type SearchFilters = {
    subject_id?: string | null
    grade_level_id?: string | null
    resource_type_code?: string | null
    resource_id?: string | null
    resource_version_id?: string | null
}
export type SearchRequest = {
    query: string
    method: SearchMethod
    filters: SearchFilters
    page: number
    size: number
}
export type SearchItem = {
    resource_id: string
    resource_title: string
    resource_version_id: string
    version_number: number
    provider_name: string | null
    resource_type_code: string
    bag_id: string
    bag_title: string | null
    unit_type: string
    representation_mode: 'structured' | 'fallback'
    matched_keywords: string[]
    matched_bag_count: number
    score: number
    score_components: Partial<Record<'bow' | 'vector', number>>
    snippet: string
    page_from: number | null
    page_to: number | null
    content_unit_id: string
    context_id: string
    context_url: string
}
export type SearchResponse = {
    query: string
    method: SearchMethod
    page: number
    size: number
    total: number
    total_pages: number
    items: SearchItem[]
    ranking_algorithm: string
    representation_unit: 'content_bag'
    ready_bag_count: number
    message: 'OK' | 'NO_READY_INDEX' | 'QUERY_NO_KEYWORDS'
}

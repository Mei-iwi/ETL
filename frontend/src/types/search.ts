export type SearchMethod = 'bow' | 'vector' | 'hybrid'
export type SearchFilters = {
    resource_format?: string | null
    domain?: string | null
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
    window_pages?: number
}
export type SearchItem = {
    resource_id: string
    resource_title: string
    resource_version_id: string
    version_number: number
    provider_name: string | null
    resource_type_code: string
    bag_id: string | null
    bag_title: string | null
    unit_type: string | null
    representation_mode: 'structured' | 'fallback' | 'metadata'
    matched_keywords: string[]
    matched_bag_count: number
    score: number
    score_components: Record<string, number>
    snippet: string
    page_from: number | null
    page_to: number | null
    content_unit_id: string | null
    context_id: string | null
    context_url: string | null
    match_origin?: 'METADATA' | 'CONTENT' | 'BOTH'
    matched_fields?: string[]
    evidence_kind?: 'METADATA_FIELD' | 'CONTENT_CHUNK'
    evidence_text?: string
    resource_format?: string
    can_view_file?: boolean
    context_evidence?: 'lexical' | 'semantic' | 'bag' | null
    meaning_variants?: string[]
    lexical_evidence?: { query_phrase: string; matched_phrase: string; concept_id: string | null; relation: string; field: string; weight: number; sources: { name: string; identifier: string }[] }[]
}
export type SearchResponse = {
    search_session?: { id: string; ttl_seconds: number }
    page_window?: SearchResponse[]
    total_exact?: boolean
    session_status?: 'UNAVAILABLE'
    branch_status?: Record<string, string>
    query_expansion?: Record<string, unknown> | null
    search_fingerprint?: string | null
    readiness?: { lexical_ready_bags: number; dense_ready_bags: number; eligible_versions: number; ready_versions: number; reindex_required: boolean } | null
    query: string
    method: SearchMethod
    page: number
    size: number
    total: number
    total_pages: number
    items: SearchItem[]
    ranking_algorithm: string
    vector_index?: {
        model_id: string
        revision: string
        dimension: number
        strategy: 'raw' | 'contextual'
        index_ids: string[]
        rerank_enabled: boolean
    } | null
    representation_unit: 'content_bag'
    ready_bag_count: number
    message: 'OK' | 'NO_READY_INDEX' | 'NO_ACCESSIBLE_CONTENT' | 'QUERY_NO_KEYWORDS' | 'PUBLIC_ACCESS_UNCONFIRMED'
    access_scope?: 'public' | 'local_demo' | 'authenticated_admin' | 'authenticated_user'
}

export type ChunkContext = {
    access_scope?: 'public' | 'local_demo' | 'authenticated_admin' | 'authenticated_user'
    resource_id: string
    resource_title: string
    resource_version_id: string
    version_number: number
    context_id: string
    content_unit_id: string
    bag_id: string
    neighbors: number
    heading_path: { content_unit_id: string; title: string | null; unit_type: string }[]
    chunks: {
        id: string
        content_unit_id: string
        unit_chunk_sequence: number
        char_start: number
        char_end: number
        page_from: number | null
        page_to: number | null
        text: string
        selected: boolean
    }[]
}

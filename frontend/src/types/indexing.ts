export type IndexRequestResult = {
    resource_version_id: string
    index_run_id: string
    index_status: 'PENDING' | 'RUNNING' | 'READY'
    action: 'QUEUED' | 'IN_PROGRESS' | 'ALREADY_INDEXED'
    status_url: string
}

export type ResourceOption = { id: string; title: string }
export type VersionOption = { id: string; resource_id: string; version_number: number }

export type ChunkItem = {
    id: string
    content_unit_id: string
    chunk_sequence: number
    unit_chunk_sequence: number
    page_from: number | null
    page_to: number | null
    char_start: number
    char_end: number
    text: string
    bow_top_terms: { term: string; frequency: number }[]
    bow_term_count: number
    bow_length: number
    vector_ready: boolean
    embedding_model: string
    embedding_dimension: number
    review_status: string
    retrieval_eligible: boolean
}

export type ChunksResult = {
    resource_version_id: string
    index_run_id: string | null
    index_status: string
    total: number
    offset: number
    limit: number
    items: ChunkItem[]
}

export type IndexStatusResult = {
    resource_version_id: string
    processing_status: string
    index_status: 'CONTENT_NOT_READY' | 'NOT_STARTED' | 'PENDING' | 'RUNNING' | 'READY' | 'FAILED' | 'STALE' | 'BLOCKED_RUNTIME'
    auto_enabled: boolean
    latest_run_id: string | null
    published_run_id: string | null
    chunk_count: number
    bow_ready: boolean
    vector_ready: boolean
    algorithm: string
    chunk_size_words: number
    chunk_overlap_words: number
    embedding_model: string | null
    embedding_dimension: number | null
    started_at: string | null
    finished_at: string | null
    error_code: string | null
    message: string
}

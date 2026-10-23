export interface ReconciliationRunReport {
    run_id: string
    stream_name: string
    status: 'RUNNING' | 'COMPLETED' | 'BLOCKED' | 'FAILED'
    started_at: string
    finished_at?: string | null
    source_count: number
    target_count: number
    matched_count: number
    mismatched_count: number
    match_rate_percent: number
    source_total_bytes?: number | null
    target_total_bytes?: number | null
    source_table_hash?: string | null
    target_table_hash?: string | null
    summary_meta?: {
        mismatch_ratio?: number
        max_allowed_ratio?: number
        guard_alert?: string
        reason?: string
        dry_run?: boolean
    } | null
}

export interface ReconciliationLatestReport {
    latest_run_id?: string | null
    last_reconciled_at?: string | null
    overall_status: string
    overall_match_rate_percent: number
    total_source_records: number
    total_target_records: number
    total_matched_records: number
    total_mismatched_records: number
    total_source_bytes: number
    total_target_bytes: number
    stream_reports: ReconciliationRunReport[]
}

export interface DiscrepancyItem {
    id: string
    run_id: string
    record_id: string
    stream_name: string
    discrepancy_flag: string
    source_value?: Record<string, any> | null
    target_value?: Record<string, any> | null
    detected_at: string
    resolution_status: 'UNRESOLVED' | 'AUTO_HEALED' | 'IGNORED'
}

export interface DiscrepancyListResponse {
    items: DiscrepancyItem[]
    total: number
    page: number
    page_size: number
    total_pages: number
}

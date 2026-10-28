export interface ReconciliationRunReport {
    run_id: string
    stream_name: string
    status: 'RUNNING' | 'COMPLETED' | 'BLOCKED' | 'FAILED' | 'PARTIAL'
    mode?: 'INCREMENTAL' | 'FULL'
    execution_time_ms?: number
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
        mode?: 'INCREMENTAL' | 'FULL'
        manual_full_scan?: boolean
        cycle_not_verified_count?: number
        cycle_observed_file_bytes?: number
        cycle_observed_file_count?: number
        execution_time_ms?: number
        mismatch_ratio?: number
        max_allowed_ratio?: number
        guard_alert?: string
        reason?: string
        dry_run?: boolean
        unverified_source_files?: number
        not_verified_count?: number
        sha256_not_checked_count?: number
        observed_file_bytes?: number
        observed_file_count?: number
        coverage_status?: string
        unverified_metadata_records?: number
        verified_file_count?: number
        verification_reason_counts?: Record<string, number>
        processed_files?: number
        total_files?: number
        verified_bytes?: number
    } | null
}

export interface ReconcileTriggerResponse {
    status: string
    executed_at: string
    dry_run: boolean
    mode: 'INCREMENTAL' | 'FULL'
    execution_time_ms: number
    total_streams: number
    reports: ReconciliationRunReport[]
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

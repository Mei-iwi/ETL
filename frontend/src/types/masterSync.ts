export type MasterSyncStream = 'all' | 'resources' | 'users' | 'grades' | 'subjects'
export type MasterSyncMode = 'INCREMENTAL' | 'FULL'

export type MasterSyncRequest = {
    stream: MasterSyncStream
    full: boolean
}

export type MasterSyncRunResult = {
    run_id: string
    records_read: number
    inserted: number
    updated: number
    skipped: number
    failed: number
}

export type MasterSyncResponse = Record<string, MasterSyncRunResult>

export type MasterSyncRunStatus = MasterSyncRunResult & {
    stream_name: Exclude<MasterSyncStream, 'all'>
    sync_mode: 'FULL' | 'INCREMENTAL' | null
    status: 'RUNNING' | 'COMPLETED' | 'FAILED'
    started_at: string
    finished_at: string | null
    error_message: string | null
}

export type MasterSyncStatusResponse = {
    file_sync_supported: boolean
    file_sync_error: string | null
    file_worker_healthy: boolean
    auto_supported: boolean
    auto_enabled: boolean
    interval_seconds: number
    worker_healthy: boolean
    auto_running: boolean
    last_auto_run_at: string | null
    next_run_at: string | null
    auto_error: string | null
    auto_all_enabled: boolean
    auto_streams: Array<{
        stream_name: Exclude<MasterSyncStream, 'all'>
        enabled: boolean
        sync_mode: MasterSyncMode
    }>
    runs: MasterSyncRunStatus[]
}

export type SourceSyncFilesResponse = {
    items: Array<{
        source_version_id: string
        resource_id: string
        resource_title: string | null
        source_version_number: number
        source_lifecycle_status: string
        updated_at: string
        import_status: 'UNTRACKED' | 'PENDING' | 'FETCHING' | 'IMPORTED' | 'PROCESSING' |
            'RETRY_WAIT' | 'RETRY_PROCESSING' | 'FAILED' | 'RETRY_EXHAUSTED'
        retry_eligible: boolean
        import_job_id: string | null
        etl_status: string | null
        retry_count: number | null
        last_error_code: string | null
        target_version_id: string | null
        processing_job_id: string | null
    }>
    next_cursor: string | null
    file_sync_supported: boolean
    file_sync_error: string | null
    file_worker_healthy: boolean
}

export type SourceFilesBatchRetryResponse = {
    accepted_count: number
    skipped_count: number
    conflict_count: number
    next_cursor: string | null
}

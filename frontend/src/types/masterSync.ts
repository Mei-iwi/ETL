export type MasterSyncStream = 'all' | 'resources' | 'users' | 'grades' | 'subjects'

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
        sync_mode: 'INCREMENTAL'
    }>
    runs: MasterSyncRunStatus[]
}

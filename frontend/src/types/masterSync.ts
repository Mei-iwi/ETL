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

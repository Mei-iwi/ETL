import type { MasterSyncMode, MasterSyncResponse, MasterSyncRunResult, MasterSyncRunStatus } from '../../types/masterSync'

export type ManualSyncResult = { response: MasterSyncResponse; mode: MasterSyncMode }
export type DisplaySyncRun = MasterSyncRunResult & {
    sync_mode: MasterSyncMode | null
    status: MasterSyncRunStatus['status'] | 'PENDING_STATUS'
    started_at: string | null
    finished_at: string | null
}

export function selectSyncResults(runs: MasterSyncRunStatus[], manual: ManualSyncResult | null) {
    const selected: Record<string, DisplaySyncRun> = {}
    const ordered = [...runs].sort((a, b) => b.started_at.localeCompare(a.started_at) || b.run_id.localeCompare(a.run_id))
    for (const run of ordered) {
        if (!selected[run.stream_name]) selected[run.stream_name] = run
    }
    for (const [stream, data] of Object.entries(manual?.response ?? {})) {
        // An old poll must not replace counters from the action response. Once the
        // same run_id is visible, use server timestamps/status and subsequent runs.
        if (!runs.some((run) => run.stream_name === stream && run.run_id === data.run_id)) {
            selected[stream] = { ...data, sync_mode: manual!.mode, status: 'PENDING_STATUS', started_at: null, finished_at: null }
        }
    }
    return selected
}

export function configuredModeLabel(modes: MasterSyncMode[]) {
    const unique = new Set(modes)
    return unique.size > 1 ? 'Theo từng nhóm' : unique.has('FULL') ? 'Full'
        : unique.has('INCREMENTAL') ? 'Incremental' : 'Chưa xác định'
}

export function acceptStatusPoll(active: boolean, aborted: boolean, revision: number, currentRevision: number, submitting: boolean) {
    return active && !aborted && revision === currentRevision && !submitting
}

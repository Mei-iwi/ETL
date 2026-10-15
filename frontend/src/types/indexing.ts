export type IndexRequestResult = {
    resource_version_id: string
    index_run_id: string
    index_status: 'PENDING' | 'RUNNING' | 'READY'
    action: 'QUEUED' | 'IN_PROGRESS' | 'ALREADY_INDEXED'
    status_url: string
}

export type ResourceOption = { id: string; title: string }
export type VersionOption = { id: string; resource_id: string; version_number: number }

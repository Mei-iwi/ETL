export type JobStatus =
    | 'PENDING'
    | 'RUNNING'
    | 'COMPLETED'
    | 'COMPLETED_WITH_ERRORS'
    | 'FAILED'

export type ProcessingStatus =
    | 'OCR_PENDING'
    | 'OCR_FAILED'
    | 'FINALIZATION_PENDING'
    | 'FINALIZING'
    | 'FINALIZATION_FAILED'
    | 'CONTENT_READY'

export type ProcessResult = {
    resource_version_id: string
    job_id: string
    job_status: JobStatus
    action: string
    processing_status: ProcessingStatus
    status_url: string
}

export type ProcessingJobResult = {
    id: string
    resource_version_id: string
    status: JobStatus
    processing_status: ProcessingStatus
    total_pages: number
    completed_pages: number
    failed_pages: number
    started_at: string | null
    completed_at: string | null
    updated_at: string
    stage_runs: unknown[]
}

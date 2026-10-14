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
    error_code?: string | null
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

export type ContentUnit = {
    start_seconds?: number | null
    end_seconds?: number | null
    source_format?: string | null
    id: string
    resource_version_id: string
    parent_unit_id: string | null
    unit_type: string
    title: string | null
    sequence_no: number
    page_from: number | null
    page_to: number | null
    review_status: string
    retrieval_eligible: boolean
    text: string
    page_result_id: string
}

export type ContentUnitsResult = {
    items: ContentUnit[]
    total: number
    offset: number
    limit: number
}

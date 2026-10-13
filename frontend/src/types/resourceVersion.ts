export type CreateResourceVersionResponse = {
    action: 'CREATED' | 'NO_CHANGE'
    resource_id: string
    resource_version_id: string
    parent_version_id: string | null
    version_number: number
    lifecycle_status: string
    file_name: string
    file_size: number
    mime_type: string
    source_hash: string
    uploaded_by: string | null
    created_at: string
}

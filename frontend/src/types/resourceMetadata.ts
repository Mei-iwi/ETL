export type PublicationStatus = 'DRAFT' | 'PUBLISHED' | 'UNPUBLISHED'

export interface UpdateResourceMetadataRequest {
    title: string
    resource_type_code: string
    resource_type_name_vi: string
    provider_id: string
    provider_name: string
    provider_type: string
    subject_id: string | null
    grade_level_id: string | null
    publication_status: PublicationStatus
}

export interface UpdateResourceMetadataResponse {
    id: string
    title: string | null
    resource_type_code: string
    resource_type_name_vi: string
    provider_id: string | null
    provider_name: string | null
    provider_type: string | null
    subject_id: string | null
    grade_level_id: string | null
    publication_status: string
    uploaded_by: string | null
    updated_at: string
}

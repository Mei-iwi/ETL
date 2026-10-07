export type MasterStream = 'all' | 'grades' | 'subjects' | 'users' | 'resources';

export type SyncMode = 'incremental' | 'full';

export type SyncRunStatus = 'RUNNING' | 'COMPLETED' | 'FAILED';

export interface CheckpointInfo {
  last_updated_at: string | null;
  last_id: string | null;
}

export interface SyncRun {
  id: string;
  stream_name: MasterStream;
  started_at: string;
  finished_at: string | null;
  status: SyncRunStatus;
  records_read: number;
  inserted: number;
  updated: number;
  skipped: number;
  failed: number;
  checkpoint_before: CheckpointInfo | null;
  checkpoint_after: CheckpointInfo | null;
  error_message: string | null;
}

export interface SyncCheckpoint {
  stream_name: 'grades' | 'subjects' | 'users' | 'resources';
  last_updated_at: string | null;
  last_id: string | null;
  updated_at: string;
}

export interface MasterLearningResource {
  id: string; // max 30 chars
  title: string;
  provider_id: string;
  provider_name: string;
  provider_type: string;
  provider_is_active: boolean;
  resource_type_id: string;
  resource_type_code: string;
  resource_type_name_vi: string;
  publication_status: 'PUBLISHED' | 'DRAFT' | 'ARCHIVED';
  is_active: boolean;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  subjects: Array<{
    id: string;
    name: string;
    is_primary: boolean;
  }>;
  grades: Array<{
    id: string;
    name_vi: string;
  }>;
}

export interface ResourceVersion {
  id: string; // ULID
  resource_id: string;
  parent_version_id: string | null;
  version_number: number;
  lifecycle_status: 'INGESTED';
  storage_bucket: string;
  storage_object_key: string;
  source_hash: string; // SHA-256
  uploaded_by: string | null;
  created_at: string;
  updated_at: string;
}

export type OCRJobStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'COMPLETED_WITH_ERRORS'
  | 'FAILED';

export interface OCRJob {
  id: string; // ULID
  resource_version_id: string;
  triggered_by: string | null;
  status: OCRJobStatus;
  total_pages: number;
  completed_pages: number;
  failed_pages: number;
  started_at: string | null;
  completed_at: string | null;
  postprocess_enqueued_at: string | null;
}

export type OCRPageTaskStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';

export interface OCRPageTask {
  id: string; // ULID
  job_id: string;
  page_num: number;
  local_image_path: string | null;
  status: OCRPageTaskStatus;
  worker_id: string | null;
  retry_count: number; // max 3
  error_message: string | null;
  heartbeat_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  claim_token: string | null;
}

export interface OCRPageResult {
  id: string; // ULID
  page_task_id: string;
  raw_text: string;
  normalized_text: string | null;
  confidence: number | null;
  engine_name: string; // e.g., 'native_pdf_text' | 'fake'
  engine_version: string | null;
  result_metadata: Record<string, any> | null;
  created_at: string;
  updated_at: string;
}

export type ETLStageStatus = 'RUNNING' | 'COMPLETED' | 'FAILED';

export interface ETLStageRun {
  id: string; // ULID
  resource_version_id: string;
  stage: 'POSTPROCESS';
  status: ETLStageStatus;
  started_at: string;
  finished_at: string | null;
  input_fingerprint: string | null; // SHA-256
  error_message: string | null;
  coverage: {
    successful_pages: number;
    total_pages: number;
    failed_pages: number;
  } | null;
}

export interface ContentUnit {
  id: string; // ULID
  resource_version_id: string;
  parent_unit_id: string | null;
  unit_type: 'PAGE_TEXT';
  title: string | null;
  sequence_no: number;
  page_from: number;
  page_to: number;
  review_status: 'UNREVIEWED';
  retrieval_eligible: false;
  text: string;
  page_result_id: string;
  created_at: string;
  updated_at: string;
}

export interface SystemHealth {
  postgres: {
    status: 'Connected' | 'Error';
    detail: string;
  };
  api: {
    status: 'Running' | 'Disabled' | 'Error';
    url: string;
    correlation_id: string | null;
  };
  source_adapter: {
    mode: 'json' | 'production';
    status: 'Active' | 'BLOCKED_SOURCE_MAPPING';
    detail: string;
  };
  ocr_engine: {
    mode: 'native' | 'fake';
    status: 'Active' | 'BLOCKED_OCR_RUNTIME';
    detail: string;
  };
  admin_api: {
    enabled: boolean;
    status: 'Enabled (Dev Mode)' | 'Disabled (Default)';
    detail: string;
  };
}

export interface IngestionResult {
  action: 'CREATED' | 'NO_CHANGE';
  version: ResourceVersion;
  reason?: string;
}

export interface EtlStatusResponse {
  resource: MasterLearningResource;
  version: ResourceVersion;
  latest_version: ResourceVersion;
  ocr_job: OCRJob | null;
  stage_runs: ETLStageRun[];
  content_unit_count: number;
}

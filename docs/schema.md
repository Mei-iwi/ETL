# Schema Master Data và ETL

Master tables là denormalized snapshots: đọc một resource/user/grade không cần JOIN catalog nguồn.
Parent ID và audit fields giữ khả năng truy vết; parent đổi thì source phát lại child qua effective watermark.

15 bảng + alembic_version. FK dùng RESTRICT, không cascade xóa lịch sử.
Resource 1:N versions; version 1:N jobs và content_units; job 1:N page_tasks;
page_task 1:0..1 result; content_unit 1:1 body; result 1:N body references.
Hai bảng quan hệ master dùng composite PK (N:N). Version/unit có self FK nullable.

Migration 0001 tạo 10 bảng ERD; 0002 tạo sync_checkpoints/sync_runs;
0003 tạo ocr_page_results; 0004 tạo etl_stage_runs/content_unit_texts.
Đợt rà soát 02/10/2026 không đổi schema/migration. postprocess_enqueued_at được giữ vì tương thích,
không ghi mới khi chưa có queue; dùng etl_stage_runs để xem trạng thái xử lý.

## master_learning_resources

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| provider_id | VARCHAR(30) | no |  |
| provider_name | VARCHAR(255) | no |  |
| provider_type | VARCHAR(30) | no |  |
| provider_is_active | BOOLEAN | no |  |
| provider_deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| provider_deleted_by | VARCHAR(30) | yes |  |
| provider_created_at | TIMESTAMP WITH TIME ZONE | yes |  |
| provider_updated_at | TIMESTAMP WITH TIME ZONE | yes |  |
| resource_type_id | VARCHAR(30) | no |  |
| resource_type_code | VARCHAR(50) | no |  |
| resource_type_name_vi | VARCHAR(150) | no |  |
| resource_type_is_active | BOOLEAN | no |  |
| resource_type_deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| resource_type_deleted_by | VARCHAR(30) | yes |  |
| resource_type_created_at | TIMESTAMP WITH TIME ZONE | yes |  |
| resource_type_updated_at | TIMESTAMP WITH TIME ZONE | yes |  |
| created_by | VARCHAR(30) | yes |  |
| title | VARCHAR(500) | no |  |
| publication_status | VARCHAR(30) | no |  |
| is_active | BOOLEAN | no |  |
| deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| deleted_by | VARCHAR(30) | yes |  |

## master_users

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| email | VARCHAR(255) | no |  |
| hashed_password | VARCHAR(255) | no |  |
| full_name | VARCHAR(255) | no |  |
| role_id | VARCHAR(30) | no |  |
| role_code | VARCHAR(50) | no |  |
| role_name | VARCHAR(255) | no |  |
| role_description | VARCHAR(255) | yes |  |
| role_is_active | BOOLEAN | no |  |
| role_deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| role_deleted_by | VARCHAR(30) | yes |  |
| role_created_at | TIMESTAMP WITH TIME ZONE | yes |  |
| role_updated_at | TIMESTAMP WITH TIME ZONE | yes |  |
| is_active | BOOLEAN | no |  |
| email_verified_at | TIMESTAMP WITH TIME ZONE | yes |  |
| deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| deleted_by | VARCHAR(30) | yes |  |

## master_grade_levels

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| education_level_id | VARCHAR(30) | no |  |
| grade_code | VARCHAR(50) | no |  |
| grade_name_vi | VARCHAR(100) | no |  |
| grade_ordinal | SMALLINT | yes |  |
| grade_created_by | VARCHAR(30) | yes |  |
| grade_updated_by | VARCHAR(30) | yes |  |
| grade_is_active | BOOLEAN | no |  |
| grade_deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| grade_deleted_by | VARCHAR(30) | yes |  |
| grade_created_at | TIMESTAMP WITH TIME ZONE | yes |  |
| grade_updated_at | TIMESTAMP WITH TIME ZONE | yes |  |
| education_code | VARCHAR(30) | no |  |
| education_name_vi | VARCHAR(100) | no |  |
| education_ordinal | SMALLINT | yes |  |
| education_is_active | BOOLEAN | no |  |
| education_deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| education_deleted_by | VARCHAR(30) | yes |  |
| education_created_at | TIMESTAMP WITH TIME ZONE | yes |  |
| education_updated_at | TIMESTAMP WITH TIME ZONE | yes |  |

## subjects

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| grade_id | VARCHAR(30) | yes | FK → master_grade_levels.id |
| subject_name | VARCHAR(255) | no |  |
| subject_slug | VARCHAR(255) | no |  |
| is_active | BOOLEAN | no |  |
| deleted_at | TIMESTAMP WITH TIME ZONE | yes |  |
| deleted_by | VARCHAR(30) | yes |  |

## resource_subjects

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| resource_id | VARCHAR(30) | no | PK; FK → master_learning_resources.id |
| subject_id | VARCHAR(30) | no | PK; FK → subjects.id |
| is_primary | BOOLEAN | no |  |

## resource_grade_levels

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| resource_id | VARCHAR(30) | no | PK; FK → master_learning_resources.id |
| grade_level_id | VARCHAR(30) | no | PK; FK → master_grade_levels.id |

## resource_versions

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| resource_id | VARCHAR(30) | no | FK → master_learning_resources.id |
| parent_version_id | VARCHAR(30) | yes | FK → resource_versions.id |
| version_number | INTEGER | no |  |
| lifecycle_status | VARCHAR(30) | no |  |
| storage_bucket | VARCHAR(255) | no |  |
| storage_object_key | VARCHAR(500) | no |  |
| source_hash | VARCHAR(64) | no |  |
| uploaded_by | VARCHAR(30) | yes |  |
| assigned_by | VARCHAR(30) | yes |  |
| assigned_reviewer_id | VARCHAR(30) | yes |  |
| assigned_at | TIMESTAMP WITH TIME ZONE | yes |  |
| pre_ocr_approved_by | VARCHAR(30) | yes |  |
| pre_ocr_approved_at | TIMESTAMP WITH TIME ZONE | yes |  |
| final_approved_by | VARCHAR(30) | yes |  |
| final_approved_at | TIMESTAMP WITH TIME ZONE | yes |  |
| rejection_reason | TEXT | yes |  |

CHECK: `version_number > 0`.

UNIQUE: resource_id, version_number.

Index `ix_versions_hash`: source_hash.

Index `ix_versions_resource_created`: resource_id, created_at.

## content_units

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| resource_version_id | VARCHAR(30) | no | FK → resource_versions.id |
| parent_unit_id | VARCHAR(30) | yes | FK → content_units.id |
| unit_type | VARCHAR(30) | no |  |
| title | VARCHAR(500) | yes |  |
| sequence_no | INTEGER | no |  |
| page_from | INTEGER | yes |  |
| page_to | INTEGER | yes |  |
| review_status | VARCHAR(30) | no |  |
| retrieval_eligible | BOOLEAN | no |  |

CHECK: `page_from > 0 AND page_to >= page_from`.

CHECK: `sequence_no > 0`.

UNIQUE: resource_version_id, sequence_no.

## ocr_jobs

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| resource_version_id | VARCHAR(30) | no | FK → resource_versions.id |
| triggered_by | VARCHAR(30) | yes |  |
| status | VARCHAR(30) | no |  |
| total_pages | INTEGER | no |  |
| completed_pages | INTEGER | no |  |
| failed_pages | INTEGER | no |  |
| started_at | TIMESTAMP WITH TIME ZONE | yes |  |
| completed_at | TIMESTAMP WITH TIME ZONE | yes |  |
| postprocess_enqueued_at | TIMESTAMP WITH TIME ZONE | yes |  |

CHECK: `total_pages >= 0 AND completed_pages >= 0 AND failed_pages >= 0 AND completed_pages + failed_pages <= total_pages`.

Index `uq_active_ocr_version`: resource_version_id (unique).

Partial unique index chỉ áp dụng status PENDING/RUNNING.

## ocr_page_tasks

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| job_id | VARCHAR(30) | no | FK → ocr_jobs.id |
| page_num | INTEGER | no |  |
| local_image_path | VARCHAR(500) | yes |  |
| status | VARCHAR(30) | no |  |
| worker_id | VARCHAR(100) | yes |  |
| retry_count | INTEGER | no |  |
| error_message | TEXT | yes |  |
| heartbeat_at | TIMESTAMP WITH TIME ZONE | yes |  |
| started_at | TIMESTAMP WITH TIME ZONE | yes |  |
| completed_at | TIMESTAMP WITH TIME ZONE | yes |  |
| claim_token | VARCHAR(30) | yes |  |

CHECK: `page_num > 0 AND retry_count >= 0`.

UNIQUE: job_id, page_num.

Index `ix_tasks_claim`: status, created_at.

Index `ix_tasks_heartbeat`: status, heartbeat_at.

## sync_checkpoints

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| stream_name | VARCHAR(30) | no | PK |
| last_updated_at | TIMESTAMP WITH TIME ZONE | yes |  |
| last_id | VARCHAR(30) | yes |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |

## sync_runs

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| stream_name | VARCHAR(30) | no |  |
| started_at | TIMESTAMP WITH TIME ZONE | no |  |
| finished_at | TIMESTAMP WITH TIME ZONE | yes |  |
| status | VARCHAR(30) | no |  |
| records_read | INTEGER | no |  |
| inserted | INTEGER | no |  |
| updated | INTEGER | no |  |
| skipped | INTEGER | no |  |
| failed | INTEGER | no |  |
| checkpoint_before | JSONB | yes |  |
| checkpoint_after | JSONB | yes |  |
| error_message | TEXT | yes |  |

## ocr_page_results

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |
| updated_at | TIMESTAMP WITH TIME ZONE | no |  |
| page_task_id | VARCHAR(30) | no | FK → ocr_page_tasks.id |
| raw_text | TEXT | no |  |
| normalized_text | TEXT | yes |  |
| confidence | NUMERIC | yes |  |
| engine_name | VARCHAR(100) | no |  |
| engine_version | VARCHAR(100) | yes |  |
| result_metadata | JSONB | yes |  |

UNIQUE: page_task_id.

## etl_stage_runs

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| id | VARCHAR(30) | no | PK |
| resource_version_id | VARCHAR(30) | no | FK → resource_versions.id |
| stage | VARCHAR(30) | no |  |
| status | VARCHAR(30) | no |  |
| started_at | TIMESTAMP WITH TIME ZONE | no |  |
| finished_at | TIMESTAMP WITH TIME ZONE | yes |  |
| input_fingerprint | VARCHAR(64) | yes |  |
| error_message | TEXT | yes |  |
| created_at | TIMESTAMP WITH TIME ZONE | no |  |

Index `ix_stage_version`: resource_version_id, stage, created_at.

## content_unit_texts

| Cột | Kiểu PostgreSQL | Nullable | Khóa |
|---|---|---|---|
| content_unit_id | VARCHAR(30) | no | PK; FK → content_units.id |
| page_result_id | VARCHAR(30) | no | FK → ocr_page_results.id |
| text | TEXT | no |  |


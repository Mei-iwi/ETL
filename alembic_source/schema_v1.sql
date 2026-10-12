-- Frozen source ERD from prompt appendix A/B. No Master runtime tables here.
CREATE TYPE provider_type AS ENUM ('PUBLISHER','SCHOOL','ORGANIZATION','INTERNAL');
CREATE TYPE publication_status AS ENUM ('DRAFT','PUBLISHED','ARCHIVED');
CREATE TYPE lifecycle_status AS ENUM ('UPLOADED','PENDING_PRE_REVIEW','QUEUED','PROCESSING','READY_FOR_REVIEW','APPROVED','REJECTED','FAILED','CANCELLED');
CREATE TYPE ocr_job_status AS ENUM ('QUEUED','PROCESSING','POSTPROCESSING','COMPLETED','FAILED','CANCELLED');
CREATE TYPE ocr_page_task_status AS ENUM ('PENDING','PROCESSING','COMPLETED','FAILED','CANCELLED');
CREATE TYPE content_unit_type AS ENUM ('PART','CHAPTER','LESSON','SECTION','SUBSECTION');
CREATE TYPE content_review_status AS ENUM ('PENDING','APPROVED','REJECTED');

CREATE TABLE id_counters (
    prefix VARCHAR(8) NOT NULL, period VARCHAR(4) NOT NULL, last_seq INTEGER NOT NULL,
    PRIMARY KEY (prefix, period), CHECK (last_seq >= 0)
);
CREATE TABLE education_levels (
    id VARCHAR(30) PRIMARY KEY, code VARCHAR(30) NOT NULL UNIQUE,
    name_vi VARCHAR(100) NOT NULL, ordinal SMALLINT NOT NULL, is_active BOOLEAN NOT NULL,
    deleted_at TIMESTAMPTZ, deleted_by VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE grade_levels (
    id VARCHAR(30) PRIMARY KEY,
    education_level_id VARCHAR(30) NOT NULL REFERENCES education_levels(id) ON DELETE RESTRICT,
    code VARCHAR(30) NOT NULL UNIQUE, name_vi VARCHAR(100) NOT NULL, ordinal SMALLINT NOT NULL,
    mongo_id VARCHAR(24), created_by VARCHAR(30), updated_by VARCHAR(30),
    is_active BOOLEAN NOT NULL, deleted_at TIMESTAMPTZ, deleted_by VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE subjects (
    id VARCHAR(30) PRIMARY KEY, grade_id VARCHAR(30) NOT NULL REFERENCES grade_levels(id) ON DELETE RESTRICT,
    subject_name VARCHAR(255) NOT NULL, subject_slug VARCHAR(255) NOT NULL,
    mongo_id VARCHAR(24), is_active BOOLEAN NOT NULL, deleted_at TIMESTAMPTZ, deleted_by VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE providers (
    id VARCHAR(30) PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE,
    provider_type provider_type NOT NULL, is_active BOOLEAN NOT NULL,
    deleted_at TIMESTAMPTZ, deleted_by VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE resource_types (
    id VARCHAR(30) PRIMARY KEY, code VARCHAR(50) NOT NULL UNIQUE, name_vi VARCHAR(150) NOT NULL,
    is_active BOOLEAN NOT NULL, deleted_at TIMESTAMPTZ, deleted_by VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE roles (
    id VARCHAR(30) PRIMARY KEY, code VARCHAR(50) NOT NULL UNIQUE, name VARCHAR(255) NOT NULL,
    description VARCHAR(255), is_active BOOLEAN NOT NULL, deleted_at TIMESTAMPTZ, deleted_by VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE users (
    id VARCHAR(30) PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255), role_id VARCHAR(30) NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    is_active BOOLEAN NOT NULL, email_verified_at TIMESTAMPTZ, mongo_id VARCHAR(30) UNIQUE,
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE learning_resources (
    id VARCHAR(30) PRIMARY KEY, mongo_id VARCHAR(30) UNIQUE,
    provider_id VARCHAR(30) REFERENCES providers(id) ON DELETE RESTRICT,
    resource_type_id VARCHAR(30) NOT NULL REFERENCES resource_types(id) ON DELETE RESTRICT,
    created_by VARCHAR(30) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    title VARCHAR(500), publication_status publication_status NOT NULL, is_active BOOLEAN NOT NULL,
    deleted_at TIMESTAMPTZ, deleted_by VARCHAR(30), created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE resource_grade_levels (
    resource_id VARCHAR(30) NOT NULL REFERENCES learning_resources(id) ON DELETE RESTRICT,
    grade_level_id VARCHAR(30) NOT NULL REFERENCES grade_levels(id) ON DELETE RESTRICT,
    PRIMARY KEY (resource_id, grade_level_id)
);
CREATE TABLE resource_subjects (
    resource_id VARCHAR(30) NOT NULL REFERENCES learning_resources(id) ON DELETE RESTRICT,
    subject_id VARCHAR(30) NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    is_primary BOOLEAN NOT NULL, PRIMARY KEY (resource_id, subject_id)
);
CREATE TABLE resource_versions (
    id VARCHAR(30) PRIMARY KEY, mongo_id VARCHAR(30) UNIQUE,
    resource_id VARCHAR(30) NOT NULL REFERENCES learning_resources(id) ON DELETE RESTRICT,
    parent_version_id VARCHAR(30) REFERENCES resource_versions(id) ON DELETE RESTRICT,
    version_number INTEGER NOT NULL, lifecycle_status lifecycle_status NOT NULL,
    storage_bucket VARCHAR(255) NOT NULL, storage_object_key VARCHAR(500) NOT NULL, source_hash VARCHAR(64) NOT NULL,
    uploaded_by VARCHAR(30) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_by VARCHAR(30) REFERENCES users(id) ON DELETE RESTRICT,
    assigned_reviewer_id VARCHAR(30) REFERENCES users(id) ON DELETE RESTRICT, assigned_at TIMESTAMPTZ,
    pre_ocr_approved_by VARCHAR(30) REFERENCES users(id) ON DELETE RESTRICT, pre_ocr_approved_at TIMESTAMPTZ,
    final_approved_by VARCHAR(30) REFERENCES users(id) ON DELETE RESTRICT, final_approved_at TIMESTAMPTZ,
    rejection_reason TEXT, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE content_units (
    id VARCHAR(30) PRIMARY KEY, mongo_id VARCHAR(30) UNIQUE,
    resource_version_id VARCHAR(30) NOT NULL REFERENCES resource_versions(id) ON DELETE RESTRICT,
    parent_unit_id VARCHAR(30) REFERENCES content_units(id) ON DELETE RESTRICT,
    unit_type content_unit_type NOT NULL, title VARCHAR(500), sequence_no INTEGER NOT NULL,
    page_from INTEGER, page_to INTEGER, review_status content_review_status NOT NULL,
    retrieval_eligible BOOLEAN NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE ocr_jobs (
    id VARCHAR(30) PRIMARY KEY,
    resource_version_id VARCHAR(30) NOT NULL UNIQUE REFERENCES resource_versions(id) ON DELETE RESTRICT,
    triggered_by VARCHAR(30) NOT NULL REFERENCES users(id) ON DELETE RESTRICT, status ocr_job_status NOT NULL,
    total_pages INTEGER NOT NULL, completed_pages INTEGER NOT NULL, failed_pages INTEGER NOT NULL,
    started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, postprocess_enqueued_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE ocr_page_tasks (
    id VARCHAR(30) PRIMARY KEY, job_id VARCHAR(30) NOT NULL REFERENCES ocr_jobs(id) ON DELETE RESTRICT,
    page_num INTEGER NOT NULL, local_image_path VARCHAR(500) NOT NULL, status ocr_page_task_status NOT NULL,
    worker_id VARCHAR(100), retry_count INTEGER NOT NULL, error_message TEXT,
    heartbeat_at TIMESTAMPTZ, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX ix_source_grades_cursor ON grade_levels(updated_at,id);
CREATE INDEX ix_source_subjects_cursor ON subjects(updated_at,id);
CREATE INDEX ix_source_users_cursor ON users(updated_at,id);
CREATE INDEX ix_source_resources_cursor ON learning_resources(updated_at,id);
CREATE INDEX ix_source_grade_education ON grade_levels(education_level_id);
CREATE INDEX ix_source_subject_grade ON subjects(grade_id);
CREATE INDEX ix_source_user_role ON users(role_id);
CREATE INDEX ix_source_resource_provider ON learning_resources(provider_id);
CREATE INDEX ix_source_resource_type ON learning_resources(resource_type_id);
CREATE INDEX ix_source_resource_creator ON learning_resources(created_by);
CREATE INDEX ix_source_relation_subject ON resource_subjects(subject_id);
CREATE INDEX ix_source_relation_grade ON resource_grade_levels(grade_level_id);
CREATE INDEX ix_source_version_resource ON resource_versions(resource_id);
CREATE INDEX ix_source_version_parent ON resource_versions(parent_version_id);
CREATE INDEX ix_source_version_uploader ON resource_versions(uploaded_by);
CREATE INDEX ix_source_version_assigner ON resource_versions(assigned_by);
CREATE INDEX ix_source_version_reviewer ON resource_versions(assigned_reviewer_id);
CREATE INDEX ix_source_version_pre_approver ON resource_versions(pre_ocr_approved_by);
CREATE INDEX ix_source_version_final_approver ON resource_versions(final_approved_by);
CREATE INDEX ix_source_job_triggerer ON ocr_jobs(triggered_by);
CREATE INDEX ix_source_unit_version ON content_units(resource_version_id);
CREATE INDEX ix_source_unit_parent ON content_units(parent_unit_id);
CREATE INDEX ix_source_task_job ON ocr_page_tasks(job_id);

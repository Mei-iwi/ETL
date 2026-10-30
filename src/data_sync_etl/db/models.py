from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

from data_sync_etl.domain.core import new_id, now


class Base(DeclarativeBase):
    pass


JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class MasterLearningResource(Base):
    master_deleted_at = Column(DateTime(timezone=True), nullable=True)
    master_deleted_by = Column(String(255), nullable=True)
    master_deletion_reason = Column(String(500), nullable=True)
    deletion_revision = Column(Integer, nullable=False, default=0, server_default='0')
    source_origin = Column(String(30), nullable=False, default='UNKNOWN', server_default='UNKNOWN')
    source_state_hash = Column(String(64), nullable=True)
    source_missing_at = Column(DateTime(timezone=True), nullable=True)
    source_identity_version = Column(SmallInteger, nullable=True)
    source_backend = Column(String(64), nullable=True)
    source_namespace = Column(String(255), nullable=True)
    source_entity_type = Column(String(64), nullable=True)
    source_record_key = Column(String(255), nullable=True)
    source_identity_hash = Column(String(64), nullable=True)
    __tablename__ = "master_learning_resources"
    __table_args__ = (
        Index('uq_master_learning_resources_mongo_id', 'mongo_id', unique=True),
        Index('uq_master_learning_resources_source_identity',
              'source_namespace', 'source_backend', 'source_entity_type', 'source_record_key',
              unique=True),
    )
    mongo_id = Column(String(30), nullable=True)
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    provider_id = Column(String(30), nullable=True)
    provider_name = Column(String(255), nullable=True)
    provider_type = Column(String(30), nullable=True)
    provider_is_active = Column(Boolean, nullable=True)
    provider_deleted_at = Column(DateTime(timezone=True), nullable=True)
    provider_deleted_by = Column(String(30), nullable=True)
    provider_created_at = Column(DateTime(timezone=True), nullable=True)
    provider_updated_at = Column(DateTime(timezone=True), nullable=True)
    resource_type_id = Column(String(30), nullable=False)
    resource_type_code = Column(String(50), nullable=False)
    resource_type_name_vi = Column(String(150), nullable=False)
    resource_type_is_active = Column(Boolean, nullable=False)
    resource_type_deleted_at = Column(DateTime(timezone=True), nullable=True)
    resource_type_deleted_by = Column(String(30), nullable=True)
    resource_type_created_at = Column(DateTime(timezone=True), nullable=True)
    resource_type_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(30), nullable=True)
    title = Column(String(500), nullable=True)
    publication_status = Column(String(30), nullable=False)
    is_active = Column(Boolean, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(30), nullable=True)


class MasterUser(Base):
    source_origin = Column(String(30), nullable=False, default='UNKNOWN', server_default='UNKNOWN')
    source_state_hash = Column(String(64), nullable=True)
    source_missing_at = Column(DateTime(timezone=True), nullable=True)
    source_identity_version = Column(SmallInteger, nullable=True)
    source_backend = Column(String(64), nullable=True)
    source_namespace = Column(String(255), nullable=True)
    source_entity_type = Column(String(64), nullable=True)
    source_record_key = Column(String(255), nullable=True)
    source_identity_hash = Column(String(64), nullable=True)
    __tablename__ = "master_users"
    __table_args__ = (
        Index('uq_master_users_mongo_id', 'mongo_id', unique=True),
        Index('uq_master_users_source_identity',
              'source_namespace', 'source_backend', 'source_entity_type', 'source_record_key',
              unique=True),
    )
    mongo_id = Column(String(30), nullable=True)
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    email = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False, info={"sensitive": True})
    full_name = Column(String(255), nullable=True)
    role_id = Column(String(30), nullable=False)
    role_code = Column(String(50), nullable=False)
    role_name = Column(String(255), nullable=False)
    role_description = Column(String(255), nullable=True)
    role_is_active = Column(Boolean, nullable=False)
    role_deleted_at = Column(DateTime(timezone=True), nullable=True)
    role_deleted_by = Column(String(30), nullable=True)
    role_created_at = Column(DateTime(timezone=True), nullable=True)
    role_updated_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(30), nullable=True)


class MasterGradeLevel(Base):
    source_origin = Column(String(30), nullable=False, default='UNKNOWN', server_default='UNKNOWN')
    source_state_hash = Column(String(64), nullable=True)
    source_missing_at = Column(DateTime(timezone=True), nullable=True)
    source_identity_version = Column(SmallInteger, nullable=True)
    source_backend = Column(String(64), nullable=True)
    source_namespace = Column(String(255), nullable=True)
    source_entity_type = Column(String(64), nullable=True)
    source_record_key = Column(String(255), nullable=True)
    source_identity_hash = Column(String(64), nullable=True)
    __tablename__ = "master_grade_levels"
    __table_args__ = (
        Index('uq_master_grade_levels_source_identity',
              'source_namespace', 'source_backend', 'source_entity_type', 'source_record_key',
              unique=True),
    )
    grade_mongo_id = Column(String(24), nullable=True)
    id = Column(String(30), primary_key=True, default=new_id)
    education_level_id = Column(String(30), nullable=False)
    grade_code = Column(String(50), nullable=False)
    grade_name_vi = Column(String(100), nullable=False)
    grade_ordinal = Column(SmallInteger, nullable=True)
    grade_created_by = Column(String(30), nullable=True)
    grade_updated_by = Column(String(30), nullable=True)
    grade_is_active = Column(Boolean, nullable=False)
    grade_deleted_at = Column(DateTime(timezone=True), nullable=True)
    grade_deleted_by = Column(String(30), nullable=True)
    grade_created_at = Column(DateTime(timezone=True), nullable=True)
    grade_updated_at = Column(DateTime(timezone=True), nullable=True)
    education_code = Column(String(30), nullable=False)
    education_name_vi = Column(String(100), nullable=False)
    education_ordinal = Column(SmallInteger, nullable=True)
    education_is_active = Column(Boolean, nullable=False)
    education_deleted_at = Column(DateTime(timezone=True), nullable=True)
    education_deleted_by = Column(String(30), nullable=True)
    education_created_at = Column(DateTime(timezone=True), nullable=True)
    education_updated_at = Column(DateTime(timezone=True), nullable=True)


class Subject(Base):
    source_origin = Column(String(30), nullable=False, default='UNKNOWN', server_default='UNKNOWN')
    source_state_hash = Column(String(64), nullable=True)
    source_missing_at = Column(DateTime(timezone=True), nullable=True)
    source_identity_version = Column(SmallInteger, nullable=True)
    source_backend = Column(String(64), nullable=True)
    source_namespace = Column(String(255), nullable=True)
    source_entity_type = Column(String(64), nullable=True)
    source_record_key = Column(String(255), nullable=True)
    source_identity_hash = Column(String(64), nullable=True)
    __tablename__ = "subjects"
    __table_args__ = (
        Index("ix_subject_grade_active_deleted", "grade_id", "is_active", "deleted_at"),
        Index("ix_subjects_name", "subject_name"),
        Index("ix_subjects_slug", "subject_slug"),
        Index('uq_subjects_source_identity',
              'source_namespace', 'source_backend', 'source_entity_type', 'source_record_key',
              unique=True),
    )
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    grade_id = Column(
        String(30), ForeignKey("master_grade_levels.id", ondelete="RESTRICT"), nullable=True
    )
    subject_name = Column(String(255), nullable=False)
    subject_slug = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(30), nullable=True)


class ResourceSubject(Base):
    __tablename__ = "resource_subjects"
    __table_args__ = (Index("ix_resource_subjects_subject", "subject_id"),)
    resource_id = Column(
        String(30),
        ForeignKey("master_learning_resources.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    subject_id = Column(
        String(30), ForeignKey("subjects.id", ondelete="RESTRICT"), primary_key=True
    )
    is_primary = Column(Boolean, nullable=False)


class ResourceGradeLevel(Base):
    __tablename__ = "resource_grade_levels"
    resource_id = Column(
        String(30),
        ForeignKey("master_learning_resources.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    grade_level_id = Column(
        String(30), ForeignKey("master_grade_levels.id", ondelete="RESTRICT"), primary_key=True
    )


class ResourceVersion(Base):
    __tablename__ = "resource_versions"
    processing_options = Column(JSON_TYPE, nullable=True)
    __table_args__ = (
        Index('uq_resource_versions_mongo_id', 'mongo_id', unique=True),
        UniqueConstraint("resource_id", "version_number"),
        CheckConstraint("version_number > 0"),
        Index("ix_versions_resource_created", "resource_id", "created_at"),
        Index("ix_versions_hash", "source_hash"),
    )
    id = Column(String(30), primary_key=True, default=new_id)
    mongo_id = Column(String(30), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    resource_id = Column(
        String(30), ForeignKey("master_learning_resources.id", ondelete="RESTRICT"), nullable=False
    )
    parent_version_id = Column(
        String(30), ForeignKey("resource_versions.id", ondelete="RESTRICT"), nullable=True
    )
    version_number = Column(Integer, nullable=False)
    lifecycle_status = Column(String(30), nullable=False)
    storage_bucket = Column(String(255), nullable=False)
    storage_object_key = Column(String(500), nullable=False)
    source_hash = Column(String(64), nullable=False)
    source_file_size = Column(BigInteger, nullable=True)
    storage_file_size = Column(BigInteger, nullable=True)
    uploaded_by = Column(String(30), nullable=True)
    assigned_by = Column(String(30), nullable=True)
    assigned_reviewer_id = Column(String(30), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    pre_ocr_approved_by = Column(String(30), nullable=True)
    pre_ocr_approved_at = Column(DateTime(timezone=True), nullable=True)
    final_approved_by = Column(String(30), nullable=True)
    final_approved_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)


class ContentUnit(Base):
    __tablename__ = "content_units"
    source_locator = Column(JSON_TYPE, nullable=True)
    __table_args__ = (
        Index('uq_content_units_mongo_id', 'mongo_id', unique=True),
        UniqueConstraint("resource_version_id", "sequence_no"),
        CheckConstraint("sequence_no > 0"),
        CheckConstraint("page_from > 0 AND page_to >= page_from"),
        UniqueConstraint("resource_version_id", "id", name="uq_content_unit_version_id"),
        ForeignKeyConstraint(
            ["resource_version_id", "parent_unit_id"],
            ["content_units.resource_version_id", "content_units.id"],
            name="fk_content_parent_same_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("parent_unit_id <> id", name="ck_content_parent_not_self"),
    )
    id = Column(String(30), primary_key=True, default=new_id)
    mongo_id = Column(String(30), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    resource_version_id = Column(
        String(30), ForeignKey("resource_versions.id", ondelete="RESTRICT"), nullable=False
    )
    parent_unit_id = Column(
        String(30), ForeignKey("content_units.id", ondelete="RESTRICT"), nullable=True
    )
    unit_type = Column(String(30), nullable=False)
    title = Column(String(500), nullable=True)
    sequence_no = Column(Integer, nullable=False)
    page_from = Column(Integer, nullable=True)
    page_to = Column(Integer, nullable=True)
    review_status = Column(String(30), nullable=False)
    retrieval_eligible = Column(Boolean, nullable=False)


class OcrJob(Base):
    __tablename__ = "ocr_jobs"
    __table_args__ = (
        CheckConstraint(
            "total_pages >= 0 AND completed_pages >= 0 AND failed_pages >= 0 AND completed_pages + failed_pages <= total_pages"
        ),
        Index(
            "uq_active_ocr_version",
            "resource_version_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    resource_version_id = Column(
        String(30), ForeignKey("resource_versions.id", ondelete="RESTRICT"), nullable=False
    )
    triggered_by = Column(String(30), nullable=True)
    status = Column(String(30), nullable=False)
    total_pages = Column(Integer, nullable=False)
    completed_pages = Column(Integer, nullable=False)
    failed_pages = Column(Integer, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    postprocess_enqueued_at = Column(DateTime(timezone=True), nullable=True)


class OcrPageTask(Base):
    __tablename__ = "ocr_page_tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "page_num"),
        CheckConstraint("page_num > 0 AND retry_count >= 0"),
        Index("ix_tasks_claim", "status", "created_at"),
        Index("ix_tasks_heartbeat", "status", "heartbeat_at"),
    )
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    job_id = Column(String(30), ForeignKey("ocr_jobs.id", ondelete="RESTRICT"), nullable=False)
    page_num = Column(Integer, nullable=False)
    local_image_path = Column(String(500), nullable=True)
    status = Column(String(30), nullable=False)
    worker_id = Column(String(100), nullable=True)
    retry_count = Column(Integer, nullable=False)
    error_message = Column(Text, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    claim_token = Column(String(30), nullable=True)


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"
    stream_name = Column(String(30), primary_key=True)
    last_updated_at = Column(DateTime(timezone=True), nullable=True)
    last_id = Column(String(30), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id = Column(String(30), primary_key=True, default=new_id)
    stream_name = Column(String(30), nullable=False)
    sync_mode = Column(String(20), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(30), nullable=False)
    records_read = Column(Integer, nullable=False)
    inserted = Column(Integer, nullable=False)
    updated = Column(Integer, nullable=False)
    skipped = Column(Integer, nullable=False)
    failed = Column(Integer, nullable=False)
    checkpoint_before = Column(JSON_TYPE, nullable=True)
    checkpoint_after = Column(JSON_TYPE, nullable=True)
    error_message = Column(Text, nullable=True)


class SyncAutoConfig(Base):
    __tablename__ = "sync_auto_config"
    id = Column(String(30), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    interval_seconds = Column(Integer, nullable=False)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    worker_id = Column(String(100), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    source_ready = Column(Boolean, nullable=False, default=False)
    lease_owner = Column(String(100), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_auto_run_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(50), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class SyncAutoStream(Base):
    __tablename__ = "sync_auto_streams"
    stream_name = Column(String(30), primary_key=True)
    config_id = Column(
        String(30), ForeignKey("sync_auto_config.id", ondelete="CASCADE"), nullable=False
    )
    enabled = Column(Boolean, nullable=False, default=False)
    sync_mode = Column(String(20), nullable=False, default="INCREMENTAL", server_default="INCREMENTAL")
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class OcrPageResult(Base):
    __tablename__ = "ocr_page_results"
    __table_args__ = (UniqueConstraint("page_task_id"),)
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    page_task_id = Column(
        String(30), ForeignKey("ocr_page_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    raw_text = Column(Text, nullable=False)
    normalized_text = Column(Text, nullable=True)
    confidence = Column(Numeric, nullable=True)
    engine_name = Column(String(100), nullable=False)
    engine_version = Column(String(100), nullable=True)
    result_metadata = Column(JSON_TYPE, nullable=True)


class EtlStageRun(Base):
    __tablename__ = "etl_stage_runs"
    __table_args__ = (Index("ix_stage_version", "resource_version_id", "stage", "created_at"),)
    id = Column(String(30), primary_key=True, default=new_id)
    resource_version_id = Column(
        String(30), ForeignKey("resource_versions.id", ondelete="RESTRICT"), nullable=False
    )
    stage = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    input_fingerprint = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class ContentUnitText(Base):
    __tablename__ = "content_unit_texts"
    content_unit_id = Column(
        String(30), ForeignKey("content_units.id", ondelete="RESTRICT"), primary_key=True
    )
    page_result_id = Column(
        String(30), ForeignKey("ocr_page_results.id", ondelete="RESTRICT"), nullable=False
    )
    text = Column(Text, nullable=False)


class SourceFileImport(Base):
    __tablename__ = "source_file_imports"
    __table_args__ = (
        UniqueConstraint("source_system", "source_version_id", name="uq_source_file_identity"),
        UniqueConstraint("target_version_id", name="uq_source_file_target"),
        Index("ix_source_file_queue", "status", "next_retry_at", "lease_expires_at"),
    )
    id = Column(String(30), primary_key=True, default=new_id)
    source_system = Column(String(30), nullable=False, default="postgresql")
    source_version_id = Column(String(30), nullable=False)
    resource_id = Column(String(30), nullable=False)
    resource_title = Column(String(500), nullable=True)
    source_version_number = Column(Integer, nullable=False)
    source_lifecycle_status = Column(String(30), nullable=False)
    source_resource_active = Column(Boolean, nullable=False, default=True)
    source_updated_at = Column(DateTime(timezone=True), nullable=False)
    storage_bucket = Column(String(255), nullable=False)
    storage_object_key = Column(String(500), nullable=False)
    source_hash = Column(String(64), nullable=False)
    source_file_size = Column(BigInteger, nullable=True)
    storage_file_size = Column(BigInteger, nullable=True)
    status = Column(String(30), nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)
    manual_retries = Column(Integer, nullable=False, default=0)
    processing_retry_requested = Column(Boolean, nullable=False, default=False)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(50), nullable=True)
    claim_token = Column(String(30), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    target_version_id = Column(String(30), ForeignKey("resource_versions.id", ondelete="RESTRICT"), nullable=True)
    processing_job_id = Column(String(30), ForeignKey("ocr_jobs.id", ondelete="RESTRICT"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)


class SyncReconciliationRun(Base):
    __tablename__ = "sync_reconciliation_runs"
    __table_args__ = (
        Index("ix_reconcile_runs_stream_started", "stream_name", text("started_at DESC")),
    )
    run_id = Column(String(36), primary_key=True, default=new_id)
    stream_name = Column(String(50), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, default=now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(30), nullable=False, default="RUNNING")
    source_count = Column(Integer, nullable=False, default=0)
    target_count = Column(Integer, nullable=False, default=0)
    matched_count = Column(Integer, nullable=False, default=0)
    mismatched_count = Column(Integer, nullable=False, default=0)
    source_total_bytes = Column(BigInteger, nullable=True, default=0)
    target_total_bytes = Column(BigInteger, nullable=True, default=0)
    source_table_hash = Column(String(64), nullable=True)
    target_table_hash = Column(String(64), nullable=True)
    summary_meta = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class SyncReconciliationDiscrepancy(Base):
    __tablename__ = "sync_reconciliation_discrepancies"
    __table_args__ = (
        Index("ix_reconcile_discrepancies_run_flag", "run_id", "discrepancy_flag"),
        Index("ix_reconcile_discrepancies_stream_status", "stream_name", "resolution_status"),
        Index("ix_reconcile_discrepancies_record", "record_id"),
    )
    id = Column(String(36), primary_key=True, default=new_id)
    run_id = Column(
        String(36),
        ForeignKey("sync_reconciliation_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    record_id = Column(String(100), nullable=False)
    stream_name = Column(String(50), nullable=False)
    discrepancy_flag = Column(String(50), nullable=False)
    source_value = Column(JSON_TYPE, nullable=True)
    target_value = Column(JSON_TYPE, nullable=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, default=now)
    resolution_status = Column(String(30), nullable=False, default="UNRESOLVED")


class ReconciliationAuditJob(Base):
    __tablename__ = 'reconciliation_audit_jobs'
    __table_args__ = (Index('ix_audit_jobs_due', 'status', 'next_retry_at'),)
    id = Column(String(36), primary_key=True, default=new_id)
    job_key = Column(String(150), nullable=False, unique=True)
    audit_type = Column(String(30), nullable=False)
    stream_name = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False, default='PENDING')
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    finished_at = Column(DateTime(timezone=True))
    claim_token = Column(String(36))
    lease_expires_at = Column(DateTime(timezone=True))
    heartbeat_at = Column(DateTime(timezone=True))
    next_retry_at = Column(DateTime(timezone=True))
    attempts = Column(Integer, nullable=False, default=0)
    checkpoint = Column(JSON_TYPE, nullable=False, default=dict)
    error_code = Column(String(80))


class ReconciliationFileState(Base):
    __tablename__ = 'reconciliation_file_states'
    __table_args__ = (Index('ix_audit_file_due', 'next_due_at', 'resource_version_id'),)
    resource_version_id = Column(String(36), primary_key=True)
    last_verified_at = Column(DateTime(timezone=True))
    last_verified_hash = Column(String(64))
    last_verified_size = Column(BigInteger)
    verification_method = Column(String(40))
    observed_identity = Column(String(64))
    last_observed_revision = Column(String(64))
    last_attempt_at = Column(DateTime(timezone=True))
    next_due_at = Column(DateTime(timezone=True))
    status = Column(String(30), nullable=False)
    reason_code = Column(String(80))
    attempts = Column(Integer, nullable=False, default=0)


class ResourceDeletionEvent(Base):
    __tablename__ = 'resource_deletion_events'
    __table_args__ = (
        CheckConstraint("action IN ('DELETE','RESTORE')", name='ck_resource_deletion_action'),
        CheckConstraint('revision > 0', name='ck_resource_deletion_positive_revision'),
        UniqueConstraint('resource_id', 'revision', name='uq_resource_deletion_revision'),
        Index('ix_resource_deletion_pending', 'propagated_at', 'created_at'),
    )
    id = Column(String(30), primary_key=True, default=new_id)
    resource_id = Column(String(30), ForeignKey('master_learning_resources.id'), nullable=False)
    revision = Column(Integer, nullable=False)
    action = Column(String(10), nullable=False)
    actor_id = Column(String(255), nullable=False)
    reason = Column(String(500), nullable=True)
    origin = Column(String(30), nullable=False, default='MASTER_VISIBILITY_OVERRIDE')
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    propagated_at = Column(DateTime(timezone=True), nullable=True)


MODELS = {
    m.__tablename__: m
    for m in [
        MasterLearningResource,
        ResourceDeletionEvent,
        MasterUser,
        MasterGradeLevel,
        Subject,
        ResourceSubject,
        ResourceGradeLevel,
        ResourceVersion,
        ContentUnit,
        OcrJob,
        OcrPageTask,
        SyncCheckpoint,
        SyncRun,
        SyncAutoConfig,
        SyncAutoStream,
        OcrPageResult,
        EtlStageRun,
        ContentUnitText,
        SourceFileImport,
        SyncReconciliationRun,
        SyncReconciliationDiscrepancy,
    ]
}

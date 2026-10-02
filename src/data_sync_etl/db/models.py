from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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
    __tablename__ = "master_learning_resources"
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    provider_id = Column(String(30), nullable=False)
    provider_name = Column(String(255), nullable=False)
    provider_type = Column(String(30), nullable=False)
    provider_is_active = Column(Boolean, nullable=False)
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
    title = Column(String(500), nullable=False)
    publication_status = Column(String(30), nullable=False)
    is_active = Column(Boolean, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(30), nullable=True)


class MasterUser(Base):
    __tablename__ = "master_users"
    id = Column(String(30), primary_key=True, default=new_id)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    email = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False, info={"sensitive": True})
    full_name = Column(String(255), nullable=False)
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
    __tablename__ = "master_grade_levels"
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
    __tablename__ = "subjects"
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
    __table_args__ = (
        UniqueConstraint("resource_id", "version_number"),
        CheckConstraint("version_number > 0"),
        Index("ix_versions_resource_created", "resource_id", "created_at"),
        Index("ix_versions_hash", "source_hash"),
    )
    id = Column(String(30), primary_key=True, default=new_id)
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
    __table_args__ = (
        UniqueConstraint("resource_version_id", "sequence_no"),
        CheckConstraint("sequence_no > 0"),
        CheckConstraint("page_from > 0 AND page_to >= page_from"),
    )
    id = Column(String(30), primary_key=True, default=new_id)
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


MODELS = {
    m.__tablename__: m
    for m in [
        MasterLearningResource,
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
        OcrPageResult,
        EtlStageRun,
        ContentUnitText,
    ]
}

"""Additive, text-free replicas of component processing state.

These tables are intentionally separate from the writable legacy processing
tables.  They are not connected to the legacy worker or search path yet.
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, String

from data_sync_etl.db.models import Base


class ComponentJobRef(Base):
    __tablename__ = 'master_component_ocr_jobs'
    namespace = Column(String(255), primary_key=True)
    source_job_id = Column(String(255), primary_key=True)
    generation = Column(Integer, primary_key=True)
    source_version_id = Column(String(255), nullable=False)
    master_version_id = Column(String(30), nullable=False)
    master_resource_id = Column(String(30), nullable=False)
    source_hash = Column(String(64), nullable=False)
    processing_fingerprint = Column(String(64), nullable=False)
    status = Column(String(40), nullable=False)
    total_pages = Column(Integer, nullable=False)
    completed_pages = Column(Integer, nullable=False)
    failed_pages = Column(Integer, nullable=False)
    revision = Column(BigInteger, nullable=False)
    tombstone = Column(Boolean, nullable=False, default=False)
    synced_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index('ix_component_job_version', 'master_version_id', 'generation', 'status'),)


class ComponentPageRef(Base):
    __tablename__ = 'master_component_ocr_pages'
    namespace = Column(String(255), primary_key=True)
    source_task_id = Column(String(255), primary_key=True)
    generation = Column(Integer, primary_key=True)
    source_job_id = Column(String(255), nullable=False)
    master_version_id = Column(String(30), nullable=False)
    page_num = Column(Integer, nullable=False)
    status = Column(String(40), nullable=False)
    retry_count = Column(Integer, nullable=False)
    error_code = Column(String(80), nullable=True)
    verified = Column(Boolean, nullable=False)
    revision = Column(BigInteger, nullable=False)
    tombstone = Column(Boolean, nullable=False, default=False)
    synced_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index('ix_component_page_job', 'namespace', 'source_job_id', 'generation', 'page_num'),)


class ComponentUnitRef(Base):
    __tablename__ = 'master_component_content_units'
    namespace = Column(String(255), primary_key=True)
    source_unit_id = Column(String(255), primary_key=True)
    generation = Column(Integer, primary_key=True)
    source_version_id = Column(String(255), nullable=False)
    master_version_id = Column(String(30), nullable=False)
    master_resource_id = Column(String(30), nullable=False)
    parent_unit_id = Column(String(255), nullable=True)
    unit_type = Column(String(40), nullable=False)
    sequence_no = Column(Integer, nullable=False)
    page_from = Column(Integer, nullable=True)
    page_to = Column(Integer, nullable=True)
    review_status = Column(String(40), nullable=False)
    retrieval_eligible = Column(Boolean, nullable=False)
    text_checksum = Column(String(64), nullable=False)
    verified = Column(Boolean, nullable=False)
    revision = Column(BigInteger, nullable=False)
    tombstone = Column(Boolean, nullable=False, default=False)
    synced_at = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index('ix_component_unit_version', 'master_version_id', 'generation', 'parent_unit_id', 'sequence_no'),)

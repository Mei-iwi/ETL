"""Component-only unit provenance and completed postprocessing manifests."""
from sqlalchemy import JSON, Column, Integer, String, Table

from data_sync_etl.adapters.persistence.component_ocr_outbox import component_metadata

component_unit_provenance = Table(
    'component_ocr_unit_provenance', component_metadata,
    Column('namespace', String(255), primary_key=True),
    Column('source_unit_id', String(255), primary_key=True),
    Column('generation', Integer, primary_key=True),
    Column('source_version_id', String(255), nullable=False),
    Column('source_task_id', String(255), nullable=False),
    Column('page_num', Integer, nullable=False),
    Column('title', String(500)),
    Column('source_locator', JSON),
)

component_postprocess_manifests = Table(
    'component_ocr_postprocess_manifests', component_metadata,
    Column('namespace', String(255), primary_key=True),
    Column('source_version_id', String(255), primary_key=True),
    Column('generation', Integer, primary_key=True),
    Column('source_job_id', String(255), nullable=False),
    Column('source_hash', String(64), nullable=False),
    Column('processing_fingerprint', String(64), nullable=False),
    Column('content_fingerprint', String(64), nullable=False),
    Column('unit_count', Integer, nullable=False),
)

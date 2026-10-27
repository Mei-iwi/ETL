"""MASTER keyset inventory in enforced READ ONLY transactions, no new state/schema.

Version number orders versions only. It is NOT a fabricated monotonic ACL epoch.
The current schema has no complete revoke feed: shared publication stays blocked.
"""
from contextlib import contextmanager

from sqlalchemy import select, text

from data_sync_etl.application.processing_state import processing_state_from_stages
from data_sync_etl.db.models import (
    ContentUnit,
    EtlStageRun,
    MasterLearningResource,
    OcrJob,
    ResourceVersion,
    SourceFileImport,
)
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.metadata_search import metadata_allowed
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.ports.concept_corpus import ConceptBlocked, ResourceEvidence


def evidence_columns(model, *names):
    """Explicit projections: no account IDs, file paths, error text or payloads."""
    return [getattr(model, name) for name in names]


class MasterCorpusInventory:
    completeness_verified = False
    scope = 'verified_public'

    def __init__(self, engine, read_gate, *, max_units=100, allow_sqlite_fixture=False):
        read_gate.require()
        if engine.dialect.name != 'postgresql' and not (engine.dialect.name == 'sqlite' and allow_sqlite_fixture):
            raise ConceptBlocked('READ_ONLY_MASTER_REQUIRED')
        if not 1 <= max_units <= 1000:
            raise ValueError('Invalid unit bound')
        self._engine, self._gate, self.max_units = engine, read_gate, max_units

    @contextmanager
    def _read(self):
        self._gate.require()
        with self._engine.connect() as conn:
            if self._engine.dialect.name == 'postgresql':
                conn = conn.execution_options(isolation_level='REPEATABLE READ', postgresql_readonly=True)
            with conn.begin():
                if self._engine.dialect.name == 'postgresql' and conn.scalar(text('SHOW transaction_read_only')) != 'on':
                    raise ConceptBlocked('READ_ONLY_MASTER_REQUIRED')
                if self._engine.dialect.name == 'postgresql':
                    conn.execute(text("SELECT set_config('statement_timeout','5000',true)"))
                yield conn

    def token(self):
        raise ConceptBlocked('LIVE_REVOCATION_FEED_INCOMPLETE')

    def current(self, resource_id):
        rows = self._page(MasterLearningResource.id == resource_id, 1)
        return rows[0] if rows else None

    def page(self, after, limit):
        if not 1 <= limit <= 100:
            raise ValueError('Invalid MASTER batch')
        return self._page(MasterLearningResource.id > after, limit)

    def _page(self, condition, limit):
        access, result = SearchAccess('authenticated_user', allow_public=True), []
        with self._read() as conn:
            resources = conn.execute(select(*evidence_columns(MasterLearningResource,
                'id', 'title', 'mongo_id', 'source_origin', 'is_active', 'publication_status',
                'deleted_at', 'master_deleted_at', 'source_missing_at', 'deletion_revision', 'updated_at')).where(condition)
                .order_by(MasterLearningResource.id).limit(limit)).mappings()
            for source in resources:
                resource = dict(source)
                version = conn.execute(select(*evidence_columns(ResourceVersion,
                    'id', 'resource_id', 'version_number', 'source_hash', 'lifecycle_status', 'final_approved_at', 'updated_at'),
                    ResourceVersion.final_approved_by.is_not(None).label('final_approved_by'),
                    ResourceVersion.rejection_reason.is_not(None).label('rejection_reason'))
                    .where(ResourceVersion.resource_id == resource['id'])
                    .order_by(ResourceVersion.version_number.desc(), ResourceVersion.id.desc()).limit(1)).mappings().first()
                if version is None:
                    result.append(ResourceEvidence(resource_id=resource['id'], version_id='', revision=0,
                        domain='unspecified', fields={}, public=False, scope=self.scope,
                        active=resource['is_active'], deleted=resource_deleted(resource),
                        published=resource['publication_status'] == 'PUBLISHED', authority_fingerprint=digest(resource)))
                    continue
                version = dict(version)
                units = [dict(row) for row in conn.execute(select(*evidence_columns(ContentUnit,
                    'id', 'resource_version_id', 'review_status', 'retrieval_eligible', 'updated_at'))
                    .where(ContentUnit.resource_version_id == version['id']).order_by(ContentUnit.id)
                    .limit(self.max_units + 1)).mappings()]
                if len(units) > self.max_units:
                    raise ConceptBlocked('CORPUS_UNIT_BUDGET')
                imports = [dict(row) for row in conn.execute(select(*evidence_columns(SourceFileImport,
                    'resource_id', 'source_resource_active', 'source_version_number', 'source_hash',
                    'source_lifecycle_status', 'status', 'last_error_code', 'source_updated_at'))
                    .where(SourceFileImport.target_version_id == version['id']).limit(2)).mappings()]
                mapping = resource.get('source_origin') == 'LOCAL' and not imports
                if resource.get('source_origin') == 'POSTGRESQL' and len(imports) == 1:
                    item = imports[0]
                    mapping = bool(item['resource_id'] == resource['id'] and item['source_resource_active']
                        and item['source_version_number'] == version['version_number'] and item['source_hash'] == version['source_hash']
                        and item['source_lifecycle_status'] == 'APPROVED'
                        and item['status'] in ('METADATA_ONLY', 'IMPORTED', 'PROCESSING')
                        and item['last_error_code'] in (None, 'UNSUPPORTED_SOURCE_FORMAT'))
                approved = frozenset(unit['id'] for unit in units if access.allows(resource, version, unit)) if mapping else frozenset()
                metadata = mapping and metadata_allowed(resource, version, access)
                job = conn.execute(select(*evidence_columns(OcrJob,
                    'id', 'status', 'failed_pages', 'completed_pages', 'total_pages'))
                    .where(OcrJob.resource_version_id == version['id'])
                    .order_by(OcrJob.created_at.desc(), OcrJob.id.desc()).limit(1)).mappings().first()
                stages = [dict(row) for row in conn.execute(select(*evidence_columns(EtlStageRun,
                    'stage', 'status', 'input_fingerprint'))
                    .where(EtlStageRun.resource_version_id == version['id']).order_by(EtlStageRun.created_at.desc(), EtlStageRun.id.desc())
                    .limit(50)).mappings()]
                ready = bool(job and processing_state_from_stages(dict(job), stages) == 'CONTENT_READY')
                stamp = resource.get('updated_at')
                result.append(ResourceEvidence(resource_id=resource['id'], version_id=version['id'],
                    revision=version['version_number'], domain='unspecified', fields={'title': resource.get('title') or ''},
                    public=bool(metadata or approved), active=resource['is_active'], deleted=resource_deleted(resource),
                    published=resource['publication_status'] == 'PUBLISHED', ready=ready, lifecycle=version['lifecycle_status'],
                    metadata_approved=metadata, scope=self.scope, source_updated_at=stamp.timestamp() if stamp else 0.,
                    approved_unit_ids=approved, mongo_id=resource.get('mongo_id'),
                    authority_fingerprint=digest([resource, version, units, imports, stages, dict(job) if job else None])))
        return result

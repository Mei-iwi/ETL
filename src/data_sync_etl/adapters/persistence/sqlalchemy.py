from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Lock

from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from data_sync_etl.adapters.persistence.catalog_reader import CatalogQueryMixin
from data_sync_etl.adapters.persistence.queue_admission import admission_lock
from data_sync_etl.adapters.persistence.resource_metadata import ResourceMetadataRepositoryMixin
from data_sync_etl.adapters.persistence.search_reader import SearchRepositoryMixin
from data_sync_etl.db.models import (
    MODELS,
    ContentUnit,
    ContentUnitText,
    EtlStageRun,
    MasterGradeLevel,
    MasterLearningResource,
    MasterUser,
    OcrJob,
    OcrPageResult,
    OcrPageTask,
    ResourceGradeLevel,
    ResourceSubject,
    ResourceVersion,
    SourceFileImport,
    Subject,
    SyncAutoConfig,
    SyncAutoStream,
    SyncCheckpoint,
)
from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.resource_deletion import LOCAL_DELETION_FIELDS
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
from data_sync_etl.domain.source_ownership import state_hash

STREAMS = {
    "resources": "master_learning_resources",
    "users": "master_users",
    "grades": "master_grade_levels",
    "subjects": "subjects",
}

# SQLite is a single-process test/development fallback only.
_SYNC_LOCK = Lock()
_PROCESSING_LOCKS = [Lock() for _ in range(64)]


def utc(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class SQLAlchemyRepository(ResourceMetadataRepositoryMixin, SearchRepositoryMixin, CatalogQueryMixin):
    def __init__(self, session):
        self.session = session

    def source_metadata_page(self, stream, after=None, size=100):
        if stream not in ('users', 'resources') or type(size) is not int or not 1 <= size <= 1000:
            raise ValueError('Invalid source metadata page')
        model = MasterUser if stream == 'users' else MasterLearningResource
        key = model.id.collate('C') if self.session.bind.dialect.name == 'postgresql' else model.id
        fields = [model.id, model.mongo_id, model.is_active, model.deleted_at, model.source_origin]
        if stream == 'resources':
            fields.append(model.publication_status)
        stmt = select(*fields).order_by(key).limit(size)
        if after is not None:
            stmt = stmt.where(key > after)
        return [dict(row) for row in self.session.execute(stmt).mappings()]

    def metadata_subject_exists(self, subject_id):
        return self.session.scalar(select(MODELS['subjects'].id).where(
            MODELS['subjects'].id == subject_id, MODELS['subjects'].is_active.is_(True),
            MODELS['subjects'].deleted_at.is_(None),
        )) is not None

    def metadata_sync_state(self, stream, record_id):
        if stream not in ('users', 'resources'):
            raise ValueError('Invalid metadata stream')
        model = MasterUser if stream == 'users' else MasterLearningResource
        fields = [model.id, model.mongo_id, model.is_active, model.deleted_at, model.source_origin]
        if stream == 'resources':
            fields.append(model.publication_status)
        row = self.session.execute(select(*fields).where(model.id == record_id)).mappings().one_or_none()
        return dict(row) if row else None

    def master_metadata_page(self, stream, after=None, size=100):
        if stream not in ('users', 'resources') or type(size) is not int or not 1 <= size <= 1000:
            raise ValueError('Invalid metadata projection page')
        model = MasterUser if stream == 'users' else MasterLearningResource
        key = model.id.collate('C') if self.session.bind.dialect.name == 'postgresql' else model.id
        # Deliberately do not read passwords, emails, roles, titles or file paths.
        fields = [model.id, model.mongo_id, model.created_at, model.updated_at,
                  model.source_origin, model.is_active, model.deleted_at]
        if stream == 'resources':
            fields.append(model.publication_status)
        stmt = select(*fields).order_by(key).limit(size)
        if after is not None:
            stmt = stmt.where(key > after)
        return [{k: utc(v) for k, v in row.items()}
                for row in self.session.execute(stmt).mappings()]

    def reserve_master_mongo_identity(self, stream, record_id, expected, target, *,
                                      dry_run=False, eligible_only=False, expected_origin=None):
        if stream not in ('users', 'resources') or not isinstance(target, str) or not 1 <= len(target) <= 30 or target != target.strip():
            raise DomainError('BLOCKED_MONGO_ID_MAPPING')
        table = STREAMS[stream]
        model = MODELS[table]
        row = self.session.execute(select(model).where(
            model.id == record_id,
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if row is None or row.mongo_id not in (expected, target):
            raise DomainError('BLOCKED_MONGO_ID_MAPPING')
        if eligible_only and (row.source_origin not in ('POSTGRESQL', 'LOCAL')
                or row.source_origin != expected_origin or not row.is_active or row.deleted_at is not None
                or getattr(row, 'publication_status', None) == 'ARCHIVED'):
            raise DomainError('MONGO_METADATA_MAPPING_CHANGED')
        if row.source_origin == 'POSTGRESQL' and (
            not row.source_state_hash or row.source_state_hash != state_hash(self._dict(row))
        ):
            raise DomainError('BLOCKED_LOCAL_SOURCE_CHANGE')
        self._check_mongo_reference(model, record_id, target)
        if dry_run:
            return
        if row.mongo_id is None:
            try:
                row.mongo_id = target
                self.session.flush()
                if row.source_origin == 'POSTGRESQL':
                    # Preserve the existing hash algorithm; only accept this verified,
                    # row-locked identity reservation, never bless pre-existing drift.
                    row.source_state_hash = state_hash(self._dict(row))
                    self.session.flush()
            except IntegrityError:
                raise DomainError('BLOCKED_MONGO_ID_MAPPING') from None
        elif row.mongo_id != target:
            raise DomainError('BLOCKED_MONGO_ID_MAPPING')

    def _check_mongo_reference(self, model, record_id, target):
        if target is not None and self.session.scalar(select(model.id).where(
            model.mongo_id == target, model.id != record_id,
        ).limit(1)) is not None:
            raise DomainError('BLOCKED_MONGO_ID_MAPPING')

    def translation_candidates(self, limit=20):
        statement = (select(ResourceVersion)
                     .where(ResourceVersion.processing_options['translation_pending'].as_boolean().is_(True))
                     .join(OcrJob, OcrJob.resource_version_id == ResourceVersion.id)
                     .where(OcrJob.status.in_(['COMPLETED', 'COMPLETED_WITH_ERRORS']))
                     .order_by(ResourceVersion.updated_at, ResourceVersion.id).limit(limit))
        return [self._dict(row) for row in self.session.scalars(statement)]

    def content_units(self, version_id, *, offset=0, limit=50, search=""):
        conditions = [ContentUnit.resource_version_id == version_id]
        if search:
            conditions.append(
                or_(
                    ContentUnit.id.icontains(search, autoescape=True),
                    ContentUnitText.text.icontains(search, autoescape=True),
                )
            )
        joined = (
            select(ContentUnit, ContentUnitText)
            .join(ContentUnitText, ContentUnitText.content_unit_id == ContentUnit.id)
            .where(*conditions)
        )
        total = self.session.scalar(select(func.count()).select_from(joined.subquery()))
        rows = self.session.execute(
            joined.order_by(ContentUnit.sequence_no, ContentUnit.id).offset(offset).limit(limit)
        )
        items = []
        results = {}
        for unit, body in rows:
            item = {**self._dict(unit), 'text': body.text, 'page_result_id': body.page_result_id}
            if unit.unit_type in ('MP3_TEXT', 'MP4_TEXT'):
                if body.page_result_id not in results:
                    result = self.session.get(OcrPageResult, body.page_result_id)
                    results[body.page_result_id] = (result.result_metadata or {}) if result else {}
                metadata = results[body.page_result_id]
                for logical in metadata.get('logical_units', []):
                    if logical.get('title') == unit.title:
                        locator = logical.get('locator', {})
                        item.update({key: locator.get(key) for key in ('start_seconds', 'end_seconds', 'source_format')})
                        break
            items.append(item)
        return {
            "items": items,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def _dict(self, row):
        return {
            c.name: utc(getattr(row, c.name))
            for c in row.__table__.columns
            if not c.info.get("sensitive")
        }

    def get(self, table, key, *, lock=False):
        model = MODELS[table]
        keys = list(model.__table__.primary_key.columns)
        values = key if isinstance(key, tuple) else (key,)
        stmt = select(model).where(*(c == v for c, v in zip(keys, values, strict=True)))
        if lock:
            stmt = stmt.with_for_update()
        row = self.session.scalar(stmt.execution_options(populate_existing=True))
        return self._dict(row) if row else None

    def find(self, table, filters=None, *, order=(), limit=None, lock=False, skip_locked=False):
        model = MODELS[table]
        stmt = select(model).filter_by(**(filters or {}))
        for field in order:
            column = getattr(model, field.lstrip("-"))
            stmt = stmt.order_by(column.desc() if field.startswith("-") else column.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        if lock:
            stmt = stmt.with_for_update(skip_locked=skip_locked)
        return [
            self._dict(row)
            for row in self.session.scalars(stmt.execution_options(populate_existing=True))
        ]

    def insert(self, table, values):
        row = MODELS[table](**values)
        self.session.add(row)
        self.session.flush()
        return self._dict(row)

    def update(self, table, key, values):
        if table == 'resource_deletion_events' and set(values) != {'propagated_at'}:
            raise DomainError('DELETION_AUDIT_IMMUTABLE')
        row = self.session.get(MODELS[table], key)
        if row is None:
            raise LookupError("Target not found")
        visibility_changed = table == 'master_learning_resources' and any(
            name in values and utc(getattr(row, name)) != utc(values[name])
            for name in ('master_deleted_at', 'deleted_at', 'is_active', 'source_missing_at', 'publication_status'))
        for name, value in values.items():
            setattr(row, name, value)
        self.session.flush()
        if visibility_changed:
            self.bump_resource_visibility_epoch()

    def delete(self, table, filters):
        if table in ('master_learning_resources', 'resource_versions', 'resource_deletion_events'):
            raise DomainError('RESOURCE_HARD_DELETE_FORBIDDEN')
        self.session.execute(delete(MODELS[table]).filter_by(**filters))
        self.session.flush()

    def count(self, table, filters):
        return self.session.scalar(
            select(func.count()).select_from(MODELS[table]).filter_by(**filters)
        )
    #ham truy van chi tiet resource
    def get_resource_detail(self, resource_id: str) -> dict | None: 
        resource = self.session.scalar(
            select(MasterLearningResource).where(
                MasterLearningResource.id == resource_id,
                MasterLearningResource.deleted_at.is_(None),
                MasterLearningResource.master_deleted_at.is_(None),
                MasterLearningResource.is_active.is_(True),
            )
        )
        if not resource:
            return None

        # Lay danh sach mon hoc lien ket
        subject_rows = self.session.execute(
            select(Subject.id, Subject.subject_name, ResourceSubject.is_primary)
            .join(ResourceSubject, ResourceSubject.subject_id == Subject.id)
            .where(
                ResourceSubject.resource_id == resource_id,
                Subject.deleted_at.is_(None),
                Subject.is_active.is_(True),
            )
        ).all()
        # Lay danh sach khoi lop lien ket
        grade_rows = self.session.execute(
            select(MasterGradeLevel.id, MasterGradeLevel.grade_code, MasterGradeLevel.grade_name_vi)
            .join(ResourceGradeLevel, ResourceGradeLevel.grade_level_id == MasterGradeLevel.id)
            .where(
                ResourceGradeLevel.resource_id == resource_id,
                MasterGradeLevel.grade_deleted_at.is_(None),
                MasterGradeLevel.grade_is_active.is_(True),
            )
        ).all()
        data = self._dict(resource)
        data["subjects"] = [
            {"id": s.id, "name": s.subject_name, "is_primary": s.is_primary}
            for s in subject_rows
        ]
        data["grade_levels"] = [
            {"id": g.id, "grade_code": g.grade_code, "grade_name_vi": g.grade_name_vi}
            for g in grade_rows
        ]
        return data

    def ensure_checkpoint(self, stream):
        insert = pg_insert if self.session.bind.dialect.name == "postgresql" else sqlite_insert
        self.session.execute(
            insert(SyncCheckpoint)
            .values(stream_name=stream, updated_at=now())
            .on_conflict_do_nothing(index_elements=["stream_name"])
        )

    def source_import_page(self, after=None, size=20, retry_candidates=False, failed_only=False, count_only=False):
        model = SourceFileImport
        key = model.source_version_id
        if self.session.bind.dialect.name == "postgresql":
            key = key.collate("C")
        stmt = select(model)
        if after is not None:
            stmt = stmt.where(key > after)
        if retry_candidates:
            stmt = stmt.where(model.status.in_(["FAILED", "RETRY_EXHAUSTED", "PROCESSING"]))
        if failed_only:
            def latest_stage(stage):
                return select(EtlStageRun.status).where(
                    EtlStageRun.resource_version_id == model.target_version_id,
                    EtlStageRun.stage == stage,
                ).order_by(EtlStageRun.created_at.desc(), EtlStageRun.id.desc()).limit(1).correlate(model).scalar_subquery()

            projection = latest_stage("MONGO_PROJECTION")
            postprocess = latest_stage("POSTPROCESS")
            pending_retry = exists().where(
                EtlStageRun.resource_version_id == model.target_version_id,
                EtlStageRun.stage == "FINALIZATION_RETRY", EtlStageRun.status == "PENDING",
            )
            finalization_failed = and_(~pending_retry, or_(
                projection == "FAILED",
                and_(or_(projection.is_(None), projection != "RUNNING"), postprocess == "FAILED"),
            ))
            processing_failed = exists().where(
                OcrJob.id == model.processing_job_id,
                or_(OcrJob.status.in_(["FAILED", "COMPLETED_WITH_ERRORS"]),
                    and_(OcrJob.status == "COMPLETED", or_(
                        OcrJob.failed_pages > 0, OcrJob.completed_pages != OcrJob.total_pages,
                        finalization_failed,
                    ))),
            )
            stmt = stmt.where(or_(model.status.in_(["FAILED", "RETRY_EXHAUSTED"]),
                                  and_(model.status == "PROCESSING", processing_failed)))
        if count_only:
            return self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        return [self._dict(row) for row in self.session.scalars(stmt.order_by(key).limit(size))]

    def source_imports_by_system_page(self, source_system, after=None, size=100):
        if not isinstance(source_system, str) or not source_system or not 1 <= size <= 1000:
            raise ValueError('Invalid source import mapping page')
        key = SourceFileImport.source_version_id
        if self.session.bind.dialect.name == 'postgresql':
            key = key.collate('C')
        stmt = select(SourceFileImport).where(SourceFileImport.source_system == source_system)
        if after is not None:
            stmt = stmt.where(key > after)
        rows = self.session.scalars(stmt.order_by(key).limit(size))
        return [self._dict(row) for row in rows]

    def claim_source_import(self, stamp, token, expires_at, max_active=1):
        admission_lock(self.session, 'source-import')
        duration = expires_at - stamp
        stamp = now()
        expires_at = stamp + duration
        model = SourceFileImport
        active = and_(model.status == 'FETCHING', model.lease_expires_at > stamp)
        if self.session.scalar(select(func.count()).select_from(model).where(active)) >= max_active:
            return None
        other = aliased(model)
        # Serialize imports for a resource, including an older version waiting
        # for retry/recovery. PROCESSING is a committed mapping, not CONTENT_READY:
        # downstream work is version-scoped and retains its own finalization lock.
        # A committed mapping in RETRY_WAIT retains the existing _parent rejection
        # (PARENT_PENDING); it cannot publish a descendant while that parent retries.
        # Aggregate once instead of a correlated predecessor scan for every
        # candidate. This matters when a large queue has many independent resources.
        predecessor = select(other.resource_id,
            func.min(other.source_version_number).label('first_version')).where(or_(
                other.status.in_(('PENDING', 'FETCHING', 'IMPORTED', 'RETRY_PROCESSING')),
                and_(other.status == 'RETRY_WAIT', other.target_version_id.is_(None))
            )).group_by(other.resource_id).subquery()
        busy_resources = select(other.resource_id).where(
            other.status == 'FETCHING', other.lease_expires_at > stamp)
        stmt = select(model).outerjoin(predecessor, predecessor.c.resource_id == model.resource_id).where(
            model.resource_id.not_in(busy_resources),
            or_(predecessor.c.first_version.is_(None), model.source_version_number <= predecessor.c.first_version),
            ~exists().where(
            MasterLearningResource.id == model.resource_id, MasterLearningResource.master_deleted_at.is_not(None)), or_(
            and_(model.status.in_(["PENDING", "IMPORTED", "RETRY_PROCESSING", "RETRY_WAIT"]),
                 or_(model.next_retry_at.is_(None), model.next_retry_at <= stamp)),
            and_(model.status == "FETCHING", model.lease_expires_at <= stamp),
        )).order_by(model.resource_id, model.source_version_number, model.id).limit(1).with_for_update(skip_locked=True, of=model)
        row = self.session.scalar(stmt)
        if row is None:
            return None
        previous_status = row.status
        row.status = "FETCHING"
        row.claim_token = token
        row.lease_expires_at = expires_at
        row.updated_at = stamp
        row.attempts += 1
        self.session.flush()
        return {**self._dict(row), "previous_status": previous_status}

    def pending_live_ocr_tasks(self, max_active=1, heartbeat_timeout=120):
        admission_lock(self.session, 'ocr')
        stamp = now()
        active = and_(OcrPageTask.status == 'RUNNING',
                      OcrPageTask.heartbeat_at > stamp - timedelta(seconds=heartbeat_timeout))
        if self.session.scalar(select(func.count()).select_from(OcrPageTask).where(active)) >= max_active:
            return []
        live = exists().where(OcrJob.id == OcrPageTask.job_id,
            ResourceVersion.id == OcrJob.resource_version_id,
            MasterLearningResource.id == ResourceVersion.resource_id,
            MasterLearningResource.is_active.is_(True), MasterLearningResource.deleted_at.is_(None),
            MasterLearningResource.master_deleted_at.is_(None))
        query = select(OcrPageTask).where(OcrPageTask.status == 'PENDING', live).order_by(
            OcrPageTask.created_at, OcrPageTask.id).limit(1).with_for_update(skip_locked=True)
        return [self._dict(row) for row in self.session.scalars(query)]

    def resource_visibility_epoch(self):
        # O(1) authority epoch, committed atomically with tombstone + audit.
        return self.session.scalar(select(SyncCheckpoint.last_id).where(SyncCheckpoint.stream_name == 'resource-visibility-v1'))

    def bump_resource_visibility_epoch(self):
        # Defer the global lock until ALL resource writes/locks are done. Taking
        # it inside a multi-resource sync batch can deadlock with a delete of
        # a resource later in that batch.
        self.session.info['resource_visibility_dirty'] = True

    def flush_resource_visibility_epoch(self):
        if not self.session.info.pop('resource_visibility_dirty', False):
            return
        self.ensure_checkpoint('resource-visibility-v1')
        self.get('sync_checkpoints', 'resource-visibility-v1', lock=True)
        self.update('sync_checkpoints', 'resource-visibility-v1', {'last_id': new_id(), 'updated_at': now()})

    def ensure_auto_sync_config(self, interval_seconds):
        insert = pg_insert if self.session.bind.dialect.name == "postgresql" else sqlite_insert
        self.session.execute(
            insert(SyncAutoConfig)
            .values(
                id="master-data",
                enabled=False,
                interval_seconds=interval_seconds,
                source_ready=False,
                updated_at=now(),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        for stream in STREAMS:
            self.session.execute(
                insert(SyncAutoStream)
                .values(
                    stream_name=stream,
                    config_id="master-data",
                    enabled=False,
                    updated_at=now(),
                )
                .on_conflict_do_nothing(index_elements=["stream_name"])
            )

    def sync_upsert(self, table, values, *, source_origin=None, source_locator: SourceLocator | None = None):
        # Private comparison path may read the hash; public repository reads never return it.
        model = MODELS[table]
        referenced = table in ('master_users', 'master_learning_resources')
        row = self.session.get(model, values['id'], with_for_update=referenced or source_origin is not None, populate_existing=True)
        values = {k: v for k, v in values.items() if k not in
                  {'source_origin', 'source_state_hash', 'source_missing_at',
                   'source_identity_version', 'source_backend', 'source_namespace',
                   'source_entity_type', 'source_record_key', 'source_identity_hash'} | LOCAL_DELETION_FIELDS}
        if source_origin is not None:
            if source_origin != 'POSTGRESQL':
                raise DomainError('BLOCKED_SOURCE_OWNERSHIP')
            if row is not None:
                if row.source_origin != source_origin:
                    raise DomainError('BLOCKED_SOURCE_OWNERSHIP')
                if not row.source_state_hash or row.source_state_hash != state_hash(self._dict(row)):
                    raise DomainError('BLOCKED_LOCAL_SOURCE_CHANGE')
                created = 'grade_created_at' if table == 'master_grade_levels' else 'created_at'
                if created in values and utc(values[created]) != utc(getattr(row, created)):
                    raise DomainError('BLOCKED_SOURCE_IDENTITY')
                updated = 'grade_updated_at' if table == 'master_grade_levels' else 'updated_at'
                if values.get(updated) is not None and utc(values[updated]) < utc(getattr(row, updated)):
                    return 'SKIPPED'  # An older SOURCE event cannot undo newer lifecycle state.
            if source_locator is not None:
                identity = {
                    'source_identity_version': source_locator.version,
                    'source_backend': source_locator.backend.casefold(),
                    'source_namespace': source_locator.namespace,
                    'source_entity_type': source_locator.entity_type.casefold(),
                    'source_record_key': source_locator.source_record_id,
                    'source_identity_hash': source_identity_hash(source_locator),
                }
                if row is not None:
                    existing = {key: getattr(row, key) for key in identity}
                    # Old rows acquire the additive mapping only after the
                    # ownership/state/creation checks above. Partial identities
                    # are evidence of drift and cannot be silently replaced.
                    if all(value is None for value in existing.values()):
                        if values.get(created) is None:
                            raise DomainError('BLOCKED_SOURCE_IDENTITY')
                    elif any(value is None for value in existing.values()):
                        raise DomainError('BLOCKED_SOURCE_IDENTITY')
                    elif existing != identity:
                        raise DomainError('BLOCKED_SOURCE_IDENTITY')
                values.update(identity)
        if table == 'master_grade_levels' and values.get('grade_mongo_id') is None:
            values = {k: v for k, v in values.items() if k != 'grade_mongo_id'}
        if referenced:
            values = dict(values)
            incoming = values.get('mongo_id')
            if incoming is None:
                values.pop('mongo_id', None)  # NULL/absence never silently erase a sticky reference.
            else:
                if not isinstance(incoming, str) or not 1 <= len(incoming) <= 30 or incoming != incoming.strip():
                    raise DomainError('BLOCKED_MONGO_ID_MAPPING')
                if row is not None and row.mongo_id not in (None, incoming):
                    raise DomainError('BLOCKED_MONGO_ID_MAPPING')
                self._check_mongo_reference(model, values['id'], incoming)
        if row is None:
            inserted = self.insert(table, {**values, **({'source_origin': source_origin} if source_origin else {})})
            if source_origin:
                self.update(table, values['id'], {'source_state_hash': state_hash(inserted)})
            return "INSERTED"
        changed = {k: v for k, v in values.items() if utc(getattr(row, k)) != utc(v)}
        if not changed:
            if source_origin and row.source_missing_at is not None:
                row.source_missing_at = None
                self.session.flush()
                if table == 'master_learning_resources':
                    self.bump_resource_visibility_epoch()
            return "SKIPPED"
        for k, v in changed.items():
            setattr(row, k, v)
        self.session.flush()
        if table == 'master_learning_resources' and (
                set(changed) & {'deleted_at', 'is_active', 'publication_status'} or row.source_missing_at is not None):
            self.bump_resource_visibility_epoch()
        if source_origin:
            row.source_missing_at = None
            row.source_state_hash = state_hash(self._dict(row))
            self.session.flush()
        return "UPDATED"

    @staticmethod
    def _federation_target(table):
        return {
            'master_learning_resources': ('resources', 'master_resource_id'),
            'master_users': ('users', 'master_user_id'),
            'master_grade_levels': ('grades', 'master_grade_level_id'),
            'subjects': ('subjects', 'master_subject_id'),
        }[table]

    def _federated_mapping(self, source_id, locator):
        digest = source_identity_hash(locator)
        stream = locator.entity_type.casefold()
        table = STREAMS.get(stream)
        if table is None:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        model = MODELS[table]
        row = self.session.scalar(select(model).where(
            model.source_backend == locator.backend.casefold(),
            model.source_namespace == locator.namespace,
            model.source_entity_type == stream,
            model.source_record_key == locator.source_record_id))
        if row is None:
            return None
        if row.source_identity_version != locator.version or row.source_identity_hash != digest:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        return row.id

    def federated_master_id(self, source_id, locator):
        """Look up an existing internal MASTER ID for a related source identity."""
        mapping = self._federated_mapping(source_id, locator)
        if mapping is not None:
            return mapping
        table = STREAMS.get(locator.entity_type)
        if table is None:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        model = MODELS[table]
        row = self.session.scalar(select(model).where(
            model.source_backend == locator.backend.casefold(),
            model.source_namespace == locator.namespace,
            model.source_entity_type == locator.entity_type.casefold(),
            model.source_record_key == locator.source_record_id,
        ).limit(1))
        if row is None:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        return row.id

    def sync_upsert_federated(self, table, values, *, source_id, source_locator, allow_legacy_id=False):
        """Upsert metadata using the identity columns already on the MASTER row."""
        stream, _ = self._federation_target(table)
        if source_locator.entity_type.casefold() != stream:
            raise DomainError('SOURCE_MAPPING_REGISTRY_MISMATCH')
        digest = source_identity_hash(source_locator)
        master_id = self._federated_mapping(source_id, source_locator)
        model = MODELS[table]
        if master_id is None:
            existing = self.session.scalar(select(model).where(
                model.source_backend == source_locator.backend.casefold(),
                model.source_namespace == source_locator.namespace,
                model.source_entity_type == stream,
                model.source_record_key == source_locator.source_record_id,
            ).limit(1))
            if existing is not None:
                master_id = existing.id
            else:
                legacy = self.session.get(model, source_locator.source_record_id)
                legacy_identity = (None if legacy is None else (
                    legacy.source_identity_version, legacy.source_backend, legacy.source_namespace,
                    legacy.source_entity_type, legacy.source_record_key, legacy.source_identity_hash))
                if (allow_legacy_id and legacy is not None and legacy.source_origin == 'POSTGRESQL'
                        and legacy_identity is not None and all(value is None for value in legacy_identity)):
                    master_id = source_locator.source_record_id
                else:
                    master_id = 'F' + digest[:29]
            collision = self.session.get(model, master_id)
            if collision is not None:
                current = (collision.source_identity_version, collision.source_backend, collision.source_namespace,
                           collision.source_entity_type, collision.source_record_key, collision.source_identity_hash)
                expected = (source_locator.version, source_locator.backend.casefold(), source_locator.namespace,
                            stream, source_locator.source_record_id, digest)
                if any(value is not None for value in current) and current != expected:
                    raise DomainError('BLOCKED_SOURCE_MAPPING')
        federated_values = {**values, 'id': master_id}
        action = self.sync_upsert(table, federated_values, source_origin='POSTGRESQL',
                                  source_locator=source_locator)
        return action, master_id

    def source_checkpoint(self, source_id, stream):
        key = self._source_checkpoint_key(source_id, stream)
        row = self.session.get(SyncCheckpoint, key)
        return ({'last_updated_at': row.last_updated_at, 'last_id': row.last_id,
                 'updated_at': row.updated_at} if row else None)

    def source_checkpoints(self):
        rows = self.session.scalars(select(SyncCheckpoint).where(
            SyncCheckpoint.stream_name.contains(':')).order_by(SyncCheckpoint.stream_name))
        runs = self.session.scalars(select(MODELS['sync_runs']).where(
            MODELS['sync_runs'].stream_name.contains(':')).order_by(
                MODELS['sync_runs'].started_at.desc(), MODELS['sync_runs'].id.desc())).all()
        latest, retries, retry_streak_ended = {}, {}, set()
        for run in runs:
            key = run.stream_name
            if key not in latest:
                latest[key] = run
                retries[key] = 0
            if key in retry_streak_ended:
                continue
            if run.status == 'FAILED':
                retries[key] += 1
            else:
                retry_streak_ended.add(key)
        result = []
        for row in rows:
            source_id, stream = row.stream_name.split(':', 1)
            latest_run = latest.get(row.stream_name)
            result.append({'source_id': source_id, 'stream': stream,
                           'status': latest_run.status if latest_run else 'IDLE',
                           'retry_count': retries.get(row.stream_name, 0),
                           'safe_error_code': (latest_run.error_message if latest_run and
                                               latest_run.status == 'FAILED' and
                                               latest_run.error_message and
                                               latest_run.error_message.isupper() else None),
                           'last_updated_at': row.last_updated_at, 'last_id': row.last_id,
                           'updated_at': row.updated_at})
        return result

    @staticmethod
    def _source_checkpoint_key(source_id, stream):
        if (not isinstance(source_id, str) or not source_id or ':' in source_id
                or stream not in ('resources', 'users', 'grades', 'subjects')):
            raise DomainError('BLOCKED_SOURCE_CHECKPOINT_KEY')
        key = f'{source_id}:{stream}'
        if len(key.encode('utf-8')) > 30:
            raise DomainError('BLOCKED_SOURCE_CHECKPOINT_KEY')
        return key

    def federated_mapping_page(self, source_id, stream, *, source_backend, source_namespace,
                               after=None, size=1000):
        if not 1 <= size <= 1000:
            raise DomainError('BLOCKED_SOURCE_COVERAGE')
        target = STREAMS.get(stream)
        if target is None:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        model = MODELS[target]
        column = model.source_record_key.collate('C') if self.session.bind.dialect.name == 'postgresql' else model.source_record_key
        query = select(model).where(
            model.source_origin == 'POSTGRESQL', model.source_backend == source_backend.casefold(),
            model.source_namespace == source_namespace, model.source_entity_type == stream,
            model.source_record_key.is_not(None), model.source_missing_at.is_(None))
        if after is not None:
            query = query.where(column > after)
        rows = self.session.scalars(query.order_by(column).limit(size))
        return [{'source_id': source_id, 'stream': stream,
                 'source_record_key': row.source_record_key, 'source_namespace': row.source_namespace,
                 'source_backend': row.source_backend, 'source_identity_hash': row.source_identity_hash,
                 'master_id': row.id} for row in rows]

    def count_federated_mappings(self, source_id, stream, *, source_backend, source_namespace):
        target = STREAMS.get(stream)
        if target is None:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        model = MODELS[target]
        return self.session.scalar(select(func.count()).select_from(model).where(
            model.source_origin == 'POSTGRESQL', model.source_backend == source_backend.casefold(),
            model.source_namespace == source_namespace, model.source_entity_type == stream,
            model.source_record_key.is_not(None), model.source_missing_at.is_(None))) or 0

    def mark_federated_source_missing(self, source_id, stream, source_record_key, stamp, *,
                                      source_backend, source_namespace):
        target = {'resources': 'master_learning_resources', 'users': 'master_users',
                  'grades': 'master_grade_levels', 'subjects': 'subjects'}.get(stream)
        if target is None:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        table = MODELS[target]
        row = self.session.scalar(select(table).where(
            table.source_origin == 'POSTGRESQL', table.source_backend == source_backend.casefold(),
            table.source_namespace == source_namespace, table.source_entity_type == stream,
            table.source_record_key == source_record_key, table.source_missing_at.is_(None)
        ).with_for_update())
        if row is None:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        if (row.source_origin != 'POSTGRESQL'
                or row.source_identity_hash != source_identity_hash(SourceLocator(
                    version=row.source_identity_version, backend=source_backend, namespace=source_namespace,
                    entity_type=stream, source_record_id=source_record_key))
                or not row.source_state_hash or row.source_state_hash != state_hash(self._dict(row))):
            raise DomainError('BLOCKED_LOCAL_SOURCE_CHANGE')
        active_field, deleted_field = (('grade_is_active', 'grade_deleted_at') if stream == 'grades'
                                       else ('is_active', 'deleted_at'))
        values = {active_field: False, 'source_missing_at': stamp}
        if getattr(row, deleted_field) is None:
            values[deleted_field] = stamp
        self.update(target, row.id, values)
        row = self.session.get(table, row.id, populate_existing=True)
        row.source_state_hash = state_hash(self._dict(row))
        self.session.flush()
        return row.id

    def ensure_source_checkpoint(self, source_id, stream):
        key = self._source_checkpoint_key(source_id, stream)
        row = self.session.get(SyncCheckpoint, key)
        if row is None:
            row = SyncCheckpoint(stream_name=key, last_updated_at=None, last_id=None, updated_at=now())
            self.session.add(row)
            self.session.flush()
        return row

    def update_source_checkpoint(self, source_id, stream, values):
        row = self.ensure_source_checkpoint(source_id, stream)
        if 'last_id' in values and values['last_id'] is not None and len(values['last_id']) > 30:
            raise DomainError('BLOCKED_SOURCE_CHECKPOINT_ID')
        for key in ('last_updated_at', 'last_id'):
            if key in values:
                setattr(row, key, values[key])
        row.updated_at = now()
        self.session.flush()

    def reconciliation_page(self, table, after=None, size=100):
        if table not in STREAMS.values() or not 1 <= size <= 1000:
            raise ValueError('Invalid reconciliation bounds')
        model = MODELS[table]
        column = model.id.collate('C') if self.session.bind.dialect.name == 'postgresql' else model.id
        query = select(model).order_by(column).limit(size)
        if after is not None:
            query = query.where(column > after)
        return [self._dict(row) for row in self.session.scalars(query)]

    def stale_tasks(self, cutoff):
        stmt = (
            select(OcrPageTask)
            .where(OcrPageTask.status == "RUNNING", OcrPageTask.heartbeat_at < cutoff)
            .order_by(OcrPageTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return [self._dict(row) for row in self.session.scalars(stmt)]

    def index_candidates(self):
        model = MODELS["etl_stage_runs"]
        rows = self.session.scalars(select(model).where(
            model.stage == "INDEX_BUILD", model.status.in_(("PENDING", "RUNNING")),
            exists().where(ResourceVersion.id == model.resource_version_id,
                MasterLearningResource.id == ResourceVersion.resource_id,
                MasterLearningResource.master_deleted_at.is_(None), MasterLearningResource.deleted_at.is_(None),
                MasterLearningResource.is_active.is_(True))
        ).order_by(model.created_at, model.id).limit(100)).all()
        return [self._dict(row) for row in rows]

    def health(self):
        self.session.execute(text("SELECT 1"))

    def list_resources(
            self,
            *,
            keyword: str | None,
            subject_id: str | None,
            grade_level_id: str | None,
            resource_type_code: str | None,
            offset: int,
            limit: int,
            allowed_resource_ids: frozenset[str] | None = None,
            deleted_only: bool = False,
    ) -> tuple[list[dict], int]:
        resouce = MasterLearningResource

        conditions = [
            resouce.is_active.is_(True),
            resouce.deleted_at.is_(None),
            resouce.master_deleted_at.is_(None),
            resouce.publication_status == 'PUBLISHED',
        ]
        if deleted_only:
            # Admin-only route checks this before invoking the listing use case.
            # Include SOURCE-restricted items too; restore still enforces SOURCE policy.
            conditions = [resouce.master_deleted_at.is_not(None)]
        if allowed_resource_ids is not None:
            conditions.append(resouce.id.in_(allowed_resource_ids))

        if keyword and keyword.strip():
            term = keyword.strip()

            term = (
                term.replace('\\', '\\\\')
                    .replace('%', '\\%')
                    .replace('_', '\\_')
            )

            conditions.append(
                resouce.title.ilike(f'%{term}%', escape='\\')
            )

        if subject_id:
            conditions.append(
                exists().where(
                    ResourceSubject.resource_id == resouce.id,
                    ResourceSubject.subject_id == subject_id,
                    Subject.id == ResourceSubject.subject_id,
                    Subject.is_active.is_(True),
                    Subject.deleted_at.is_(None),
                )
            )

        if grade_level_id: 
            conditions.append(
                exists().where(
                    ResourceGradeLevel.resource_id == resouce.id,
                    ResourceGradeLevel.grade_level_id == grade_level_id,
                    MasterGradeLevel.id == ResourceGradeLevel.grade_level_id,
                    MasterGradeLevel.grade_is_active.is_(True),
                    MasterGradeLevel.grade_deleted_at.is_(None),
                )
            )
        if resource_type_code:
            conditions.append(
                resouce.resource_type_code == resource_type_code
            )
        total_stmt = (
            select(func.count())
            .select_from(resouce)
            .where(*conditions)
        )

        total = self.session.scalar(total_stmt) or 0

        stmt = (
            select(resouce)
            .where(*conditions)
            .order_by(
                resouce.updated_at.desc(),
                resouce.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.scalars(stmt).all()

        return [self._dict(row) for row in rows], int(total)


class SQLAlchemyUnitOfWork:
    def reconciliation_engine(self, **options):
        from data_sync_etl.adapters.persistence.reconciliation_engine import ReconciliationEngine
        return ReconciliationEngine(self, **options)

    def file_reconciliation_jobs(self, engine):
        from data_sync_etl.adapters.persistence.file_reconciliation import FileReconciliationJobs
        return FileReconciliationJobs(engine)

    def __init__(self, session_factory):
        self.sessions = session_factory

    @contextmanager
    def __call__(self):
        with self.sessions.begin() as session:
            repo = SQLAlchemyRepository(session)
            yield repo
            repo.flush_resource_visibility_epoch()

    @contextmanager
    def metadata_projection_lock(self, stream):
        # Session-scoped lock on an autocommit connection: no PG transaction
        # remains open while the metadata projector performs Mongo RPCs.
        name = 'master-mongo:' + stream
        with self.sessions() as session:
            engine = session.get_bind()
            if engine.dialect.name != 'postgresql':
                with self.processing_lock(name) as acquired:
                    yield acquired
                return
            key = int.from_bytes(sha256(('etl-finalize:' + name).encode()).digest()[:8],
                                 'big', signed=True)
            with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                acquired = bool(connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}))
                try:
                    yield acquired
                finally:
                    if acquired:
                        connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})

    @contextmanager
    def sync_lock(self):
        key = int.from_bytes(sha256(b'master-data-sync').digest()[:8], 'big', signed=True)
        with self.sessions() as session:
            dialect = session.get_bind().dialect.name
            if dialect == 'postgresql':
                with session.begin():
                    acquired = session.scalar(
                        text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}
                    )
                    yield bool(acquired)
            elif dialect == 'sqlite':
                acquired = _SYNC_LOCK.acquire(blocking=False)
                try:
                    yield acquired
                finally:
                    if acquired:
                        _SYNC_LOCK.release()
            else:
                raise RuntimeError('Master sync locks require PostgreSQL')

    @contextmanager
    def processing_lock(self, version_id: str):
        key = int.from_bytes(sha256(('etl-finalize:' + version_id).encode()).digest()[:8],
                             'big', signed=True)
        with self.sessions() as session:
            dialect = session.get_bind().dialect.name
            if dialect == 'postgresql':
                # Same lock key and cross-process exclusion, without an idle
                # transaction throughout file hashing, OCR finalization or Mongo RPC.
                # A disconnected owner loses its session lock just as before.
                with session.get_bind().connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                    acquired = bool(connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}))
                    try:
                        yield acquired
                    finally:
                        if acquired:
                            connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
            elif dialect == 'sqlite':
                lock = _PROCESSING_LOCKS[key % len(_PROCESSING_LOCKS)]
                acquired = lock.acquire(blocking=False)
                try:
                    yield acquired
                finally:
                    if acquired:
                        lock.release()
            else:
                raise RuntimeError('Processing locks require PostgreSQL')

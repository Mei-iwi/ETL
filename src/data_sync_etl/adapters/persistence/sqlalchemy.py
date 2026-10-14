from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from threading import Lock

from sqlalchemy import and_, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data_sync_etl.adapters.persistence.resource_metadata import ResourceMetadataRepositoryMixin
from data_sync_etl.db.models import (
    MODELS,
    ContentUnit,
    ContentUnitText,
    EtlStageRun,
    MasterGradeLevel,
    MasterLearningResource,
    OcrJob,
    OcrPageResult,
    OcrPageTask,
    ResourceGradeLevel,
    ResourceSubject,
    SourceFileImport,
    Subject,
    SyncAutoConfig,
    SyncAutoStream,
    SyncCheckpoint,
)
from data_sync_etl.domain.core import now

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


class SQLAlchemyRepository(ResourceMetadataRepositoryMixin):
    def __init__(self, session):
        self.session = session

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
        row = self.session.get(MODELS[table], key)
        if row is None:
            raise LookupError("Target not found")
        for name, value in values.items():
            setattr(row, name, value)
        self.session.flush()

    def delete(self, table, filters):
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
            )
        ).all()
        # Lay danh sach khoi lop lien ket
        grade_rows = self.session.execute(
            select(MasterGradeLevel.id, MasterGradeLevel.grade_code, MasterGradeLevel.grade_name_vi)
            .join(ResourceGradeLevel, ResourceGradeLevel.grade_level_id == MasterGradeLevel.id)
            .where(
                ResourceGradeLevel.resource_id == resource_id,
                MasterGradeLevel.grade_deleted_at.is_(None),
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

    def source_import_page(self, after=None, size=20, retry_candidates=False, failed_only=False):
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
        return [self._dict(row) for row in self.session.scalars(stmt.order_by(key).limit(size))]

    def claim_source_import(self, stamp, token, expires_at):
        model = SourceFileImport
        stmt = select(model).where(or_(
            and_(model.status.in_(["PENDING", "IMPORTED", "RETRY_PROCESSING", "RETRY_WAIT"]),
                 or_(model.next_retry_at.is_(None), model.next_retry_at <= stamp)),
            and_(model.status == "FETCHING", model.lease_expires_at <= stamp),
        )).order_by(model.resource_id, model.source_version_number, model.id).limit(1).with_for_update(skip_locked=True)
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

    def sync_upsert(self, table, values):
        # Private comparison path may read the hash; public repository reads never return it.
        model = MODELS[table]
        row = self.session.get(model, values["id"])
        if row is None:
            self.insert(table, values)
            return "INSERTED"
        changed = {k: v for k, v in values.items() if utc(getattr(row, k)) != utc(v)}
        if not changed:
            return "SKIPPED"
        for k, v in changed.items():
            setattr(row, k, v)
        self.session.flush()
        return "UPDATED"

    def stale_tasks(self, cutoff):
        stmt = (
            select(OcrPageTask)
            .where(OcrPageTask.status == "RUNNING", OcrPageTask.heartbeat_at < cutoff)
            .order_by(OcrPageTask.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return [self._dict(row) for row in self.session.scalars(stmt)]

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
            limit: int
    ) -> tuple[list[dict], int]:
        resouce = MasterLearningResource

        conditions = [
            resouce.is_active.is_(True),
            resouce.deleted_at.is_(None),
            resouce.publication_status == 'PUBLISHED',
        ]

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
    def __init__(self, session_factory):
        self.sessions = session_factory

    @contextmanager
    def __call__(self):
        with self.sessions.begin() as session:
            yield SQLAlchemyRepository(session)

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
                # Held across all finalization work; transaction/connection loss releases it.
                with session.begin():
                    acquired = session.scalar(
                        text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}
                    )
                    yield bool(acquired)
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

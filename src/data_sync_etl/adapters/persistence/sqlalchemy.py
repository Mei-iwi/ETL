from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, text, exists
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data_sync_etl.db.models import (
    MODELS,
    ContentUnit,
    ContentUnitText,
    OcrPageTask,
    SyncCheckpoint,
    MasterLearningResource,
    MasterGradeLevel,
    ResourceGradeLevel,
    ResourceSubject,
    Subject
)
from data_sync_etl.domain.core import now

STREAMS = {
    "resources": "master_learning_resources",
    "users": "master_users",
    "grades": "master_grade_levels",
    "subjects": "subjects",
}


def utc(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class SQLAlchemyRepository:
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
        return {
            "items": [
                {**self._dict(unit), "text": body.text, "page_result_id": body.page_result_id}
                for unit, body in rows
            ],
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

    def ensure_checkpoint(self, stream):
        insert = pg_insert if self.session.bind.dialect.name == "postgresql" else sqlite_insert
        self.session.execute(
            insert(SyncCheckpoint)
            .values(stream_name=stream, updated_at=now())
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

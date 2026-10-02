from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data_sync_etl.db.models import MODELS, OcrPageTask, SyncCheckpoint
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


class SQLAlchemyUnitOfWork:
    def __init__(self, session_factory):
        self.sessions = session_factory

    @contextmanager
    def __call__(self):
        with self.sessions.begin() as session:
            yield SQLAlchemyRepository(session)

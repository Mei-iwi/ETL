"""Read-only source snapshots with bounded keyset pages; never reads source OCR history."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError, TimeoutError

from data_sync_etl.adapters.source.aggregate import build_aggregate
from data_sync_etl.adapters.source.json_fixture import MODELS
from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.source import ResourceGradeLevelRelation, ResourceSubjectRelation
from data_sync_etl.ports.source_catalogs import SourceCatalogError
from data_sync_etl.ports.source_files import SourceVersionMetadata, SourceVersionPage

TABLES = {
    "providers": "providers",
    "resource_types": "resource_types",
    "resources": "learning_resources",
    "roles": "roles",
    "users": "users",
    "education_levels": "education_levels",
    "grades": "grade_levels",
    "subjects": "subjects",
    "resource_subjects": "resource_subjects",
    "resource_grades": "resource_grade_levels",
}
QUERIES = {
    "resources": """SELECT r.*, GREATEST(r.updated_at,p.updated_at,t.updated_at) AS effective_updated_at
        FROM learning_resources r LEFT JOIN providers p ON p.id=r.provider_id
        JOIN resource_types t ON t.id=r.resource_type_id""",
    "users": """SELECT r.*, GREATEST(r.updated_at,p.updated_at) AS effective_updated_at
        FROM users r JOIN roles p ON p.id=r.role_id""",
    "grades": """SELECT r.*, GREATEST(r.updated_at,p.updated_at) AS effective_updated_at
        FROM grade_levels r JOIN education_levels p ON p.id=r.education_level_id""",
    "subjects": "SELECT r.*, r.updated_at AS effective_updated_at FROM subjects r",
}


class SnapshotReader:
    def __init__(self, connection):
        self.connection = connection

    def reconciliation_summary(self, stream):
        table = TABLES[stream]
        relation = self.connection.execute(text('SELECT relkind, relrowsecurity FROM pg_class WHERE oid=to_regclass(:name)'), {'name': table}).one_or_none()
        if relation is None or relation[0] not in ('r', 'p') or relation[1]:
            raise DomainError('BLOCKED_SOURCE_COVERAGE')
        dependencies = {
            'resources': "SELECT 1 FROM learning_resources r LEFT JOIN resource_types t ON t.id=r.resource_type_id LEFT JOIN providers p ON p.id=r.provider_id WHERE t.id IS NULL OR (r.provider_id IS NOT NULL AND p.id IS NULL) LIMIT 1",
            'users': 'SELECT 1 FROM users r LEFT JOIN roles d ON d.id=r.role_id WHERE d.id IS NULL LIMIT 1',
            'grades': 'SELECT 1 FROM grade_levels r LEFT JOIN education_levels d ON d.id=r.education_level_id WHERE d.id IS NULL LIMIT 1',
            'subjects': 'SELECT 1 FROM subjects r LEFT JOIN grade_levels d ON d.id=r.grade_id WHERE d.id IS NULL LIMIT 1',
        }
        if self.connection.scalar(text(dependencies[stream])):
            raise DomainError('BLOCKED_DEPENDENCY')
        row = self.connection.execute(text(f'SELECT count(*), min(id COLLATE "C"), max(id COLLATE "C") FROM {table}')).one()
        return dict(count=row[0], minimum=row[1], maximum=row[2])

    def reconciliation_ids(self, stream, after, size):
        table = TABLES[stream]
        return list(self.connection.scalars(text(f'SELECT id FROM {table} WHERE CAST(:after AS varchar) IS NULL OR id COLLATE "C" > :after ORDER BY id COLLATE "C" LIMIT :size'), {'after': after, 'size': size}))

    def reconciliation_present(self, stream, ids):
        if not ids or len(ids) > 1000:
            return set()
        query = text(f'SELECT id FROM {TABLES[stream]} WHERE id IN :ids').bindparams(bindparam('ids', expanding=True))
        return set(self.connection.scalars(query, {'ids': ids}))

    @staticmethod
    def record(name, values):
        values = dict(values)
        if name not in ('users', 'resources', 'grades'):
            values.pop('mongo_id', None)
        values.pop("effective_updated_at", None)
        if name == "providers":
            values["type"] = values.pop("provider_type")
        return MODELS[name].model_validate(values)

    def lookup(self, name, source_id):
        row = (
            self.connection.execute(
                text(f"SELECT * FROM {TABLES[name]} WHERE id=:id"), {"id": source_id}
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise DomainError("BLOCKED_SOURCE_MAPPING: source dependency missing")
        return self.record(name, row)

    def get_provider(self, source_id):
        return self.lookup("providers", source_id)

    def get_resource_type(self, source_id):
        return self.lookup("resource_types", source_id)

    def get_role(self, source_id):
        return self.lookup("roles", source_id)

    def get_education_level(self, source_id):
        return self.lookup("education_levels", source_id)

    def relations(self, table, resource_id, model):
        rows = self.connection.execute(
            text(
                f"SELECT j.*, r.updated_at FROM {table} j "
                "JOIN learning_resources r ON r.id=j.resource_id WHERE j.resource_id=:id"
            ),
            {"id": resource_id},
        ).mappings()
        return [model.model_validate(row) for row in rows]

    def get_resource_subjects(self, resource_id):
        return self.relations("resource_subjects", resource_id, ResourceSubjectRelation)

    def get_resource_grade_levels(self, resource_id):
        return self.relations("resource_grade_levels", resource_id, ResourceGradeLevelRelation)


class PostgreSQLSourceAdapter:
    source_origin = 'POSTGRESQL'
    def __init__(self, url=None, *, engine=None, replay_seconds=300):
        if engine is None:
            if not url or make_url(url).get_backend_name() != "postgresql":
                raise DomainError("BLOCKED_SOURCE_MAPPING: PostgreSQL SOURCE_DATABASE_URL required")
            engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
        if engine.dialect.name != "postgresql" or replay_seconds < 0:
            raise DomainError("BLOCKED_SOURCE_MAPPING: invalid PostgreSQL source configuration")
        self.engine = engine
        self.replay_seconds = replay_seconds

    @contextmanager
    def snapshot(self, *, catalog=False):
        try:
            with (
                self.engine.connect().execution_options(
                    isolation_level="REPEATABLE READ", postgresql_readonly=True
                ) as connection,
                connection.begin(),
            ):
                if catalog:
                    connection.execute(text("SELECT set_config('statement_timeout','3000',true)"))
                role = connection.execute(
                    text(
                    "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls AS privileged "
                        "FROM pg_roles WHERE rolname=current_user"
                    )
                ).scalar_one()
                if role:
                    raise DomainError("BLOCKED_SOURCE_MAPPING: source account must be read-only")
                for table in TABLES.values():
                    if connection.scalar(
                        text(
                            "SELECT has_table_privilege(current_user, :table, 'INSERT,UPDATE,DELETE,TRUNCATE')"
                        ),
                        {"table": table},
                    ):
                        raise DomainError(
                            "BLOCKED_SOURCE_MAPPING: source account has write privileges"
                        )
                yield SnapshotReader(connection)
        except SourceCatalogError:
            raise
        except DomainError:
            if catalog:
                raise SourceCatalogError("SOURCE_ERROR") from None
            raise
        except Exception as exc:
            if catalog:
                state = getattr(getattr(exc, "orig", None), "sqlstate", None)
                unavailable = isinstance(exc, TimeoutError) or (
                    isinstance(exc, (DBAPIError, OperationalError)) and (
                        (state is not None and (state.startswith("08") or state in {"57014", "57P01", "57P02", "57P03"}))
                        or (state is None and isinstance(exc, OperationalError))))
                raise SourceCatalogError("SOURCE_UNAVAILABLE" if unavailable else "SOURCE_ERROR") from None
            raise DomainError(
                "BLOCKED_SOURCE_MAPPING: source snapshot unavailable or invalid"
            ) from None

    def batches(self, stream, cursor=None, batch_size=100):
        if stream not in QUERIES or batch_size < 1:
            raise DomainError("Invalid source stream or batch size")
        # Replay recent timestamps to tolerate short transactions committing out of order.
        # Long-running writers still require periodic full reconciliation (see docs).
        # A future source marker must not move the replay window past current writers.
        anchor = min(cursor.updated_at, datetime.now(UTC)) if cursor else None
        stamp = anchor - timedelta(seconds=self.replay_seconds) if anchor else None
        last_id = "" if self.replay_seconds else (cursor.id if cursor else "")
        with self.snapshot() as reader:
            while True:
                condition = ""
                if stamp is not None:
                    condition = 'WHERE (effective_updated_at,id COLLATE "C") > (:stamp,:last_id)'
                rows = (
                    reader.connection.execute(
                        text(
                            f"SELECT * FROM ({QUERIES[stream]}) source_rows {condition} "
                            'ORDER BY effective_updated_at,id COLLATE "C" LIMIT :limit'
                        ),
                        {"stamp": stamp, "last_id": last_id, "limit": batch_size},
                    )
                    .mappings()
                    .all()
                )
                if not rows:
                    return
                batch = [
                    build_aggregate(reader, stream, reader.record(stream, row)) for row in rows
                ]
                yield batch
                stamp, last_id = rows[-1]["effective_updated_at"], rows[-1]["id"]

    def iter_learning_resources(self, cursor=None, batch_size=100):
        return self.batches("resources", cursor, batch_size)

    def iter_users(self, cursor=None, batch_size=100):
        return self.batches("users", cursor, batch_size)

    def iter_grade_levels(self, cursor=None, batch_size=100):
        return self.batches("grades", cursor, batch_size)

    def iter_subjects(self, cursor=None, batch_size=100):
        return self.batches("subjects", cursor, batch_size)

    def get_provider(self, source_id):
        with self.snapshot() as reader:
            return reader.get_provider(source_id)

    def get_resource_type(self, source_id):
        with self.snapshot() as reader:
            return reader.get_resource_type(source_id)

    def get_role(self, source_id):
        with self.snapshot() as reader:
            return reader.get_role(source_id)

    def get_education_level(self, source_id):
        with self.snapshot() as reader:
            return reader.get_education_level(source_id)

    def get_resource_subjects(self, resource_id):
        with self.snapshot() as reader:
            return reader.get_resource_subjects(resource_id)

    def get_resource_grade_levels(self, resource_id):
        with self.snapshot() as reader:
            return reader.get_resource_grade_levels(resource_id)

    def health(self):
        with self.snapshot() as reader:
            reader.connection.execute(text("SELECT 1 FROM learning_resources LIMIT 1"))

    def metadata_identity_references(self, stream, ids):
        """Read-only reference safety check, never a source of Mongo payload fields."""
        if stream not in ('users', 'resources') or len(ids) > 1000:
            raise DomainError('Invalid metadata identity check')
        if not ids:
            return {}
        table = 'users' if stream == 'users' else 'learning_resources'
        with self.snapshot() as reader:
            rows = reader.connection.execute(text(
                f'SELECT id,mongo_id FROM {table} WHERE id IN :ids'
            ).bindparams(bindparam('ids', expanding=True)), {'ids': list(ids)})
            return {row.id: row.mongo_id for row in rows}

    def metadata_subject_exists(self, subject_id):
        with self.snapshot() as reader:
            return reader.connection.scalar(text(
                'SELECT id FROM subjects WHERE id=:id AND is_active=true AND deleted_at IS NULL'
            ), {'id': subject_id}) is not None

    def list_versions(self, after=None, size=20):
        """Read a bounded metadata page; never return storage references or hashes."""
        page = self.version_page(after, size)
        public = {"source_version_id", "resource_id", "source_version_number",
                  "source_lifecycle_status", "updated_at", "resource_title"}
        return {"items": [{k: v for k, v in row.items() if k in public} for row in page["items"]],
                "next_cursor": page["next_cursor"]}

    def version_page(self, after: str | None = None, size: int = 20) -> SourceVersionPage:
        """Private worker metadata. Storage references must never be serialized to HTTP."""
        if not 1 <= size <= 100:
            raise DomainError("Invalid source version page size")
        with self.snapshot() as reader:
            connection = reader.connection
            if not connection.scalar(text(
                "SELECT has_table_privilege(current_user, 'resource_versions', 'SELECT')"
            )) or connection.scalar(text(
                "SELECT has_table_privilege(current_user, 'resource_versions', "
                "'INSERT,UPDATE,DELETE,TRUNCATE')"
            )):
                raise DomainError("BLOCKED_SOURCE_VERSION_ACCESS")
            rows = connection.execute(text(
                "SELECT v.id AS source_version_id, v.resource_id, v.version_number AS "
                "source_version_number, v.lifecycle_status::text AS source_lifecycle_status, "
                "v.updated_at, r.title AS resource_title, v.storage_bucket, v.storage_object_key, "
                "v.source_hash, v.parent_version_id, r.is_active AS resource_active, r.deleted_at AS resource_deleted_at "
                "FROM resource_versions v JOIN learning_resources r ON r.id=v.resource_id "
                "WHERE (CAST(:after AS varchar) IS NULL OR v.id COLLATE \"C\" > :after) "
                "ORDER BY v.id COLLATE \"C\" LIMIT :limit"
            ), {"after": after, "limit": size + 1}).mappings().all()
            return {
                "items": [cast(SourceVersionMetadata, dict(row)) for row in rows[:size]],
                "next_cursor": rows[size - 1]["source_version_id"] if len(rows) > size else None,
            }

    def catalog_rows(self, kind):
        # Read selective catalog columns, never scan resource rows or write SOURCE.
        # Bound transfer and fail explicitly beyond the supported catalog budget.
        queries = {
            "providers": "SELECT id,name,provider_type::text AS provider_type,is_active,deleted_at FROM providers",
            "types": "SELECT id,code,name_vi AS name,is_active,deleted_at FROM resource_types",
        }
        if kind not in queries:
            raise ValueError("Unknown source catalog")
        with self.snapshot(catalog=True) as reader:
            rows = reader.connection.execute(text(queries[kind] + " ORDER BY id LIMIT 20001")).mappings().all()
            if len(rows) > 20000:
                raise SourceCatalogError("SOURCE_LIMIT_EXCEEDED")
            return [{**dict(row), "available": row["is_active"] and row["deleted_at"] is None}
                    for row in rows]

    def get_version(self, source_version_id: str) -> SourceVersionMetadata | None:
        """Exact private identity lookup; includes roots, no inferred parent."""
        with self.snapshot() as reader:
            connection = reader.connection
            if not connection.scalar(text(
                "SELECT has_table_privilege(current_user, 'resource_versions', 'SELECT')"
            )) or connection.scalar(text(
                "SELECT has_table_privilege(current_user, 'resource_versions', 'INSERT,UPDATE,DELETE,TRUNCATE')"
            )):
                raise DomainError("BLOCKED_SOURCE_VERSION_ACCESS")
            row = connection.execute(text(
                "SELECT v.id AS source_version_id,v.resource_id,v.version_number AS source_version_number, "
                "v.parent_version_id,v.source_hash,v.storage_bucket,v.storage_object_key,v.updated_at, "
                "v.lifecycle_status::text AS source_lifecycle_status, "
                "r.is_active AS resource_active,r.deleted_at AS resource_deleted_at "
                "FROM resource_versions v JOIN learning_resources r ON r.id=v.resource_id WHERE v.id=:id"
            ), {"id": source_version_id}).mappings().one_or_none()
            return cast(SourceVersionMetadata, dict(row)) if row else None

    def close(self):
        self.engine.dispose()

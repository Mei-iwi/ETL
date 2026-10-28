"""Bounded read-only metadata pages, independent of the sync adapter implementation."""
from contextlib import contextmanager
from datetime import UTC
from heapq import nsmallest

from sqlalchemy import bindparam, inspect, text

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.reconciliation import METADATA_FIELDS, STREAM_TABLE_MAPPING

AUTH_FIELDS = ('hashed_password', 'role_code', 'role_is_active', 'role_deleted_at', 'role_updated_at')


def resource_relations(conn, rows):
    ids = [row['id'] for row in rows]
    subjects, grades = {}, {}
    query = text('SELECT resource_id, subject_id, is_primary FROM resource_subjects WHERE resource_id IN :ids').bindparams(bindparam('ids', expanding=True))
    for resource_id, subject_id, primary in conn.execute(query, {'ids': ids}):
        subjects.setdefault(resource_id, []).append((subject_id, bool(primary)))
    query = text('SELECT resource_id, grade_level_id FROM resource_grade_levels WHERE resource_id IN :ids').bindparams(bindparam('ids', expanding=True))
    for resource_id, grade_id in conn.execute(query, {'ids': ids}):
        grades.setdefault(resource_id, []).append(grade_id)
    for row in rows:
        row['_resource_subjects'] = sorted(subjects.get(row['id'], []))
        row['_resource_grades'] = sorted(grades.get(row['id'], []))


@contextmanager
def connection(adapter):
    if hasattr(adapter, 'snapshot'):
        with adapter.snapshot() as reader:
            yield getattr(reader, 'connection', None), reader
    else:
        with adapter.engine.connect() as conn:
            if conn.dialect.name == 'postgresql':
                conn = conn.execution_options(isolation_level='REPEATABLE READ')
                conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            yield conn, None


def metadata_pages(adapter, stream, since, after, size):
    if adapter is None:
        raise DomainError('SOURCE_UNAVAILABLE')
    if hasattr(adapter, 'engine') or hasattr(adapter, 'snapshot'):
        with connection(adapter) as (conn, reader):
            if conn is None:
                reader.reconciliation_summary(stream)
                while True:
                    ids = reader.reconciliation_ids(stream, after, size)
                    if not ids:
                        return
                    if ids != sorted(set(ids)) or after and ids[0] <= after:
                        raise DomainError('SOURCE_COVERAGE_INVALID')
                    yield [{'id': key} for key in ids]
                    after = ids[-1]
            else:
                if conn.dialect.name == 'postgresql':
                    conn.execute(text("SET LOCAL statement_timeout = '5000'"))
                table = STREAM_TABLE_MAPPING[stream]['source_table']
                aliases = {'code': 'grade_code', 'name_vi': 'grade_name_vi',
                    'updated_at': 'grade_updated_at',
                    'is_active': 'grade_is_active', 'deleted_at': 'grade_deleted_at'} if stream == 'grades' else {}
                available = {c['name'] for c in inspect(conn).get_columns(table)}
                fields = ['id', 'updated_at'] + sorted(c for c in available
                    if aliases.get(c, c) in METADATA_FIELDS[stream])
                changed_at = 'updated_at'
                if stream == 'users' and 'hashed_password' in available and inspect(conn).has_table('roles'):
                    # Private comparison only. Audit serialization strips password material.
                    table = ('(SELECT u.*,r.code AS role_code,r.is_active AS role_is_active,'
                             'r.deleted_at AS role_deleted_at,r.updated_at AS role_updated_at,'
                             'CASE WHEN u.updated_at > r.updated_at THEN u.updated_at ELSE r.updated_at END '
                             'AS effective_updated_at FROM users u JOIN roles r ON r.id=u.role_id) audit_users')
                    fields += list(AUTH_FIELDS)
                    changed_at = 'effective_updated_at'
                projection = ', '.join(f'{c} AS {aliases[c]}' if c in aliases else c for c in dict.fromkeys(fields))
                if stream == 'users' and 'deleted_at' not in available:
                    # Canonical SOURCE users has no deleted_at column. The same
                    # SourceRecord contract used by sync defaults it to None.
                    projection += ', NULL AS deleted_at'
                key = 'id COLLATE "C"' if conn.dialect.name == 'postgresql' else 'id'
                while True:
                    conditions = []
                    if since is not None:
                        changed = [f'{changed_at} >= :since']
                        if stream == 'resources':
                            for relation in ('resource_subjects', 'resource_grade_levels'):
                                if inspect(conn).has_table(relation) and 'updated_at' in {
                                        c['name'] for c in inspect(conn).get_columns(relation)}:
                                    changed.append(f'id IN (SELECT resource_id FROM {relation} WHERE updated_at >= :since)')
                        conditions.append('(' + ' OR '.join(changed) + ')')
                    if after:
                        conditions.append(f'{key} > :after')
                    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
                    rows = conn.execute(text(f'SELECT {projection} FROM {table}{where} ORDER BY {key} LIMIT :size'),
                        {'since': since, 'after': after, 'size': size}).mappings().all()
                    if not rows:
                        return
                    ids = [str(r['id']) for r in rows]
                    if ids != sorted(set(ids)) or after and ids[0] <= after:
                        raise DomainError('SOURCE_COVERAGE_INVALID')
                    page = [dict(r) for r in rows]
                    if stream == 'resources':
                        resource_relations(conn, page)
                    yield page
                    after = str(rows[-1]['id'])
        return
    if hasattr(adapter, 'records') and hasattr(adapter, 'aggregate'):
        while True:
            batch = nsmallest(size, (row for row in adapter.records(stream)
                if not after or str(row.id) > after), key=lambda row: str(row.id))
            if not batch:
                return
            rows = []
            for row in batch:
                aggregate = adapter.aggregate(stream, row)
                stamp = aggregate.cursor.updated_at
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                if (since is None or stamp >= since) and (not after or str(aggregate.values['id']) > after):
                    values = dict(aggregate.values)
                    if stream == 'resources':
                        values['_resource_subjects'] = sorted((r.subject_id, r.is_primary) for r in aggregate.subjects)
                        values['_resource_grades'] = sorted(r.grade_level_id for r in aggregate.grades)
                    rows.append(values)
            after = str(batch[-1].id)
            if rows:
                yield rows
    raise DomainError('SOURCE_METADATA_UNSUPPORTED')

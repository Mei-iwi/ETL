"""Component-owned state/outbox and explicit Master projection runner.

Install the metadata only in a verified isolated component database.  The
caller manages the component transaction for state + outbox and supplies the
Master transaction and trusted identity verifier to the runner.
"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    and_,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data_sync_etl.adapters.persistence.component_ocr_projection import (
    ComponentOCRProjector,
    ProjectionError,
)
from data_sync_etl.db.ocr_component_models import (
    ComponentJobRef,
    ComponentPageRef,
    ComponentUnitRef,
)
from data_sync_etl.domain.core import DomainError

component_metadata = MetaData()

component_outbox = Table(
    'component_ocr_outbox', component_metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('namespace', String(255), nullable=False),
    Column('kind', String(10), nullable=False),
    Column('entity_id', String(255), nullable=False),
    Column('generation', Integer, nullable=False),
    Column('revision', BigInteger, nullable=False),
    Column('snapshot', JSON, nullable=False),
    Column('delivered', Boolean, nullable=False, default=False),
    Column('attempts', Integer, nullable=False, default=0, server_default='0'),
    Column('next_attempt_at', DateTime(timezone=True), nullable=True),
    Column('last_error_code', String(80), nullable=True),
    UniqueConstraint('namespace', 'kind', 'entity_id', 'generation', 'revision', name='uq_component_ocr_event'),
)

_MODELS = {'job': ComponentJobRef, 'page': ComponentPageRef, 'unit': ComponentUnitRef}
_ID = {'job': 'source_job_id', 'page': 'source_task_id', 'unit': 'source_unit_id'}
_PUBLIC_PROJECTION_ERRORS = frozenset({
    'PARENT_NOT_READY', 'IDENTITY_NOT_AUTHORIZED', 'IDENTITY_CONFLICT', 'REVISION_CONFLICT',
    'TOMBSTONE_CONFLICT', 'TERMINAL_JOB_CONFLICT', 'UNIT_NOT_VERIFIED',
})
_TABLE_NAMES = {'job': 'component_ocr_jobs', 'page': 'component_ocr_pages', 'unit': 'component_content_units'}
component_tables = {
    kind: Table(_TABLE_NAMES[kind], component_metadata,
                *(column._copy() for column in model.__table__.columns if column.name != 'synced_at'))
    for kind, model in _MODELS.items()
}


class ComponentStateError(ValueError):
    pass


def require_isolated_prototype(engine):
    """Historical component-schema prototype is limited to in-memory tests."""
    if engine.dialect.name != 'sqlite' or engine.url.database not in (None, ':memory:'):
        raise ComponentStateError('COMPONENT_PROTOTYPE_DISABLED')


def record_snapshot(connection, kind, snapshot):
    require_isolated_prototype(connection.engine)
    if (kind == 'page' and isinstance(snapshot, dict)
            and (snapshot.get('status') == 'SUCCEEDED' or snapshot.get('verified') is True)):
        raise ComponentStateError('COMPONENT_PAGE_REQUIRES_CONTENT_CHECK')
    if kind == 'unit' and isinstance(snapshot, dict) and snapshot.get('verified') is True:
        raise ComponentStateError('COMPONENT_UNIT_REQUIRES_CONTENT_CHECK')
    if kind == 'unit' and isinstance(snapshot, dict) and snapshot.get('retrieval_eligible') is True:
        raise ComponentStateError('COMPONENT_UNIT_NOT_VERIFIED')
    if kind == 'job' and isinstance(snapshot, dict) and snapshot.get('status') == 'COMPLETED':
        pages = component_tables['page']
        rows = connection.execute(select(pages).where(
            pages.c.namespace == snapshot.get('namespace'),
            pages.c.source_job_id == snapshot.get('source_job_id'),
            pages.c.generation == snapshot.get('generation'),
        )).mappings().all()
        total = snapshot.get('total_pages')
        if (type(total) is not int or total < 1 or len(rows) != total
                or snapshot.get('completed_pages') != total or snapshot.get('failed_pages') != 0
                or {row['page_num'] for row in rows} != set(range(1, total + 1))
                or any(row['status'] != 'SUCCEEDED' or not row['verified'] or row['tombstone'] for row in rows)):
            raise ComponentStateError('COMPONENT_JOB_NOT_VERIFIED')
    return _record_snapshot(connection, kind, snapshot)


def _record_snapshot(connection, kind, snapshot):
    """Atomically update a component snapshot and insert its metadata-only event.

    This call must be in the *same* component transaction as its corresponding
    authoritative processing state change. Callers must verify actor, source
    ownership and page/unit content before invoking it.
    """
    require_isolated_prototype(connection.engine)
    if kind not in _MODELS or not isinstance(snapshot, dict):
        raise ComponentStateError('INVALID_SNAPSHOT')
    fields = set(_MODELS[kind].__table__.c.keys()) - {'synced_at'}
    if set(snapshot) != fields:
        raise ComponentStateError('INVALID_FIELDS')
    if any(type(value) not in (str, int, bool, type(None)) for value in snapshot.values()):
        raise ComponentStateError('INVALID_FIELDS')
    for name in ('status', 'unit_type', 'review_status', 'error_code'):
        value = snapshot.get(name)
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9_]{1,80}', value)):
            raise ComponentStateError('INVALID_FIELDS')
    for name in ('source_hash', 'processing_fingerprint', 'text_checksum'):
        value = snapshot.get(name)
        if name in snapshot and (not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value)):
            raise ComponentStateError('INVALID_FIELDS')
    key = {'namespace': snapshot['namespace'], 'kind': kind, 'entity_id': snapshot[_ID[kind]],
           'generation': snapshot['generation']}
    if (not isinstance(key['namespace'], str) or not key['namespace'].strip()
            or not isinstance(key['entity_id'], str) or not key['entity_id'].strip()
            or type(key['generation']) is not int or key['generation'] < 1
            or type(snapshot['revision']) is not int or snapshot['revision'] < 1):
        raise ComponentStateError('INVALID_IDENTITY')
    dialect = connection.dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise ComponentStateError('UNSUPPORTED_DATABASE')
    insert = pg_insert if dialect == 'postgresql' else sqlite_insert
    table = component_tables[kind]
    natural_key = ('namespace', _ID[kind], 'generation')
    immutable = set(natural_key) | {'master_version_id'}
    immutable.update({'source_version_id', 'master_resource_id'} & fields)
    if kind == 'job':
        immutable.update({'source_hash', 'processing_fingerprint'})
    if kind == 'page':
        immutable.add('source_job_id')
    statement = insert(table).values(**snapshot)
    excluded = statement.excluded
    verified_fence = or_(table.c.verified.is_(False), excluded.verified.is_(True), excluded.tombstone.is_(True)) \
        if kind in ('page', 'unit') else True
    terminal_fence = or_(table.c.status != 'COMPLETED', excluded.status == 'COMPLETED',
                         excluded.tombstone.is_(True)) if kind == 'job' else True
    statement = statement.on_conflict_do_update(
        index_elements=[table.c[name] for name in natural_key],
        set_={name: excluded[name] for name in fields - immutable},
        where=(table.c.revision < excluded.revision)
              & and_(*(table.c[name] == excluded[name] for name in immutable))
              & or_(table.c.tombstone.is_(False), excluded.tombstone.is_(True)) & verified_fence & terminal_fence,
    )
    connection.execute(statement)
    current = connection.execute(select(table).where(
        *[table.c[name] == snapshot[name] for name in natural_key])).mappings().one()
    if any(current[name] != snapshot[name] for name in immutable):
        raise ComponentStateError('IDENTITY_CONFLICT')
    if current['tombstone'] and not snapshot['tombstone'] and current['revision'] < snapshot['revision']:
        raise ComponentStateError('TOMBSTONE_CONFLICT')
    if (kind in ('page', 'unit') and current['verified'] and not snapshot['verified']
            and not snapshot['tombstone'] and current['revision'] < snapshot['revision']):
        raise ComponentStateError('VERIFIED_CONTENT_CONFLICT')
    if (kind == 'job' and current['status'] == 'COMPLETED' and snapshot['status'] != 'COMPLETED'
            and not snapshot['tombstone'] and current['revision'] < snapshot['revision']):
        raise ComponentStateError('TERMINAL_JOB_CONFLICT')
    if current['revision'] > snapshot['revision']:
        return False
    if any(current[name] != snapshot[name] for name in fields):
        raise ComponentStateError('REVISION_CONFLICT')
    event = insert(component_outbox).values(**key, revision=snapshot['revision'], snapshot=snapshot, delivered=False)
    event = event.on_conflict_do_nothing(
        index_elements=['namespace', 'kind', 'entity_id', 'generation', 'revision'])
    connection.execute(event)
    return True


def complete_verified_page(connection, content_store, snapshot, *, source_version_id, source_hash, fingerprint):
    """Read back canonical Mongo content before committing page completion.

    Authorization, current generation and a valid worker lease must be checked
    by the caller in this component transaction. Missing or unavailable content
    is never treated as an empty page.
    """
    require_isolated_prototype(connection.engine)
    if snapshot.get('status') != 'SUCCEEDED' or snapshot.get('verified') is not True:
        raise ComponentStateError('COMPONENT_PAGE_NOT_VERIFIED')
    jobs = component_tables['job']
    parent = connection.execute(select(jobs).where(
        jobs.c.namespace == snapshot.get('namespace'),
        jobs.c.source_job_id == snapshot.get('source_job_id'),
        jobs.c.generation == snapshot.get('generation'),
    ).with_for_update()).mappings().one_or_none()
    if (parent is None or parent['tombstone'] or parent['source_version_id'] != source_version_id
            or parent['source_hash'] != source_hash or parent['processing_fingerprint'] != fingerprint
            or parent['master_version_id'] != snapshot.get('master_version_id')
            or type(snapshot.get('page_num')) is not int or not 1 <= snapshot['page_num'] <= parent['total_pages']):
        raise ComponentStateError('COMPONENT_PAGE_IDENTITY_MISMATCH')
    document = content_store.get_page(
        namespace=snapshot.get('namespace'), version_id=source_version_id,
        generation=snapshot.get('generation'), page_num=snapshot.get('page_num'),
        source_hash=source_hash, fingerprint=fingerprint,
    )
    if document is None:
        raise ComponentStateError('COMPONENT_PAGE_NOT_VERIFIED')
    return _record_snapshot(connection, 'page', snapshot)


def complete_verified_unit(connection, content_store, snapshot, *, source_hash, fingerprint):
    """Publish a verified unit only after its component content is readable."""
    require_isolated_prototype(connection.engine)
    if snapshot.get('verified') is not True:
        raise ComponentStateError('COMPONENT_UNIT_NOT_VERIFIED')
    document = content_store.get_unit(
        namespace=snapshot.get('namespace'), version_id=snapshot.get('source_version_id'),
        generation=snapshot.get('generation'), unit_id=snapshot.get('source_unit_id'),
        source_hash=source_hash, fingerprint=fingerprint,
    )
    if document is None or document.get('checksum') != snapshot.get('text_checksum'):
        raise ComponentStateError('COMPONENT_UNIT_NOT_VERIFIED')
    return _record_snapshot(connection, 'unit', snapshot)


def project_pending(component_connection, master_engine, verify_mapping, verify_isolation, *, limit=100):
    """Project locked pending events; caller commits component ACK afterwards.

    Master commit is intentionally before ACK; a crash causes replay, and the
    projector fences the duplicate. This is not a distributed transaction.
    """
    require_isolated_prototype(component_connection.engine)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ComponentStateError('INVALID_LIMIT')
    rows = component_connection.execute(
        select(component_outbox).where(
            component_outbox.c.delivered.is_(False),
            or_(component_outbox.c.next_attempt_at.is_(None),
                component_outbox.c.next_attempt_at <= datetime.now(timezone.utc)),
        )
        .order_by(component_outbox.c.id).limit(limit).with_for_update(skip_locked=True)
    ).mappings().all()
    if not rows:
        return 0
    pending, delivered = list(rows), []
    errors = {}
    while pending:
        deferred = []
        for row in pending:
            try:
                with master_engine.begin() as master:
                    if not verify_isolation(component_connection, master):
                        raise ComponentStateError('COMPONENT_MASTER_NOT_ISOLATED')
                    projector = ComponentOCRProjector(master, verify_mapping)
                    projector.apply(row['kind'], row['snapshot'])
                delivered.append(row['id'])
            except ProjectionError as exc:
                code = str(exc)
                errors[row['id']] = code if code in _PUBLIC_PROJECTION_ERRORS else 'PROJECTION_FAILED'
                deferred.append(row)
            except DomainError:
                errors[row['id']] = 'AUTHORITY_UNAVAILABLE'
                deferred.append(row)
        if len(deferred) == len(pending):
            break
        pending = deferred
    if delivered:
        component_connection.execute(update(component_outbox).where(
            component_outbox.c.id.in_(delivered)).values(delivered=True, last_error_code=None,
                                                         next_attempt_at=None))
    for row in deferred:
        attempts = row['attempts'] + 1
        delay = min(300, 2 ** min(attempts, 8))
        component_connection.execute(update(component_outbox).where(component_outbox.c.id == row['id']).values(
            attempts=attempts, last_error_code=errors[row['id']],
            next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=delay),
        ))
    return len(delivered)


class ComponentProjectionWorker:
    """Bounded, explicitly invoked worker; never obtains trust from a client."""

    def __init__(self, component_engine, master_engine, *, verify_mapping, verify_isolation):
        if not callable(verify_mapping) or not callable(verify_isolation):
            raise ComponentStateError('COMPONENT_PROJECTION_NOT_CONFIGURED')
        require_isolated_prototype(component_engine)
        self.component_engine = component_engine
        self.master_engine = master_engine
        self.verify_mapping = verify_mapping
        self.verify_isolation = verify_isolation

    def run_once(self, *, limit=100):
        with self.component_engine.begin() as component:
            return project_pending(component, self.master_engine, self.verify_mapping,
                                   self.verify_isolation, limit=limit)

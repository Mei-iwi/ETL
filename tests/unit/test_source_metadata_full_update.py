"""Exercise metadata transactions and rollback using an isolated SQL database."""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, text

from data_sync_etl.adapters.source.postgresql_writeback import PostgreSQLSourceMetadataWriter
from data_sync_etl.application.source_writeback_state import canonical_source_hash
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
from data_sync_etl.domain.source_ownership import state_hash


@pytest.fixture
def full_writer(tmp_path):
    contract = json.loads(open('deploy/config/source-write-contract.full-metadata.proposal.json', encoding='utf-8').read())
    contract['allowed_roles'] = ['ADMIN']
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps(contract), encoding='utf-8')
    writer = PostgreSQLSourceMetadataWriter('postgresql+psycopg://localhost/test', path)
    writer.engine.dispose()
    engine = create_engine('sqlite://')

    @event.listens_for(engine, 'connect')
    def functions(connection, _):
        connection.create_function('set_config', 3, lambda *args: None)
        connection.create_function('now', 0, lambda: datetime.now(UTC).isoformat())

    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
        connection.execute(text('CREATE TABLE public.learning_resources '
                                '(id TEXT PRIMARY KEY,title TEXT,provider_id TEXT,resource_type_id TEXT,'
                                'publication_status TEXT,updated_at TEXT)'))
        connection.execute(text("INSERT INTO public.learning_resources VALUES ('RES001','Old','P1','T1','DRAFT','initial')"))
        for table in ('providers', 'resource_types', 'grade_levels'):
            connection.execute(text(f'CREATE TABLE public.{table} (id TEXT PRIMARY KEY,is_active BOOLEAN,deleted_at TEXT)'))
        connection.execute(text('CREATE TABLE public.subjects (id TEXT PRIMARY KEY,grade_id TEXT,is_active BOOLEAN,deleted_at TEXT)'))
        for table, ids in (('providers', ('P1','P2')), ('resource_types', ('T1','T2')), ('grade_levels', ('G1','G2'))):
            for value in ids:
                connection.execute(text(f'INSERT INTO public.{table} VALUES (:id,true,NULL)'), {'id': value})
        connection.execute(text("INSERT INTO public.subjects VALUES ('S1','G1',true,NULL),('S2','G2',true,NULL)"))
        connection.execute(text('CREATE TABLE public.resource_subjects '
                                '(resource_id TEXT,subject_id TEXT,is_primary BOOLEAN,UNIQUE(resource_id,subject_id))'))
        connection.execute(text('CREATE TABLE public.resource_grade_levels '
                                '(resource_id TEXT,grade_level_id TEXT,UNIQUE(resource_id,grade_level_id))'))
        connection.execute(text("INSERT INTO public.resource_subjects VALUES ('RES001','S1',true)"))
        connection.execute(text("INSERT INTO public.resource_grade_levels VALUES ('RES001','G1')"))
    writer.engine = engine
    # PostgreSQL projection/locking has its own integration tests. Here the
    # real UPDATE/relation SQL and transaction boundary run on isolated SQLite.
    writer._canonical_row = lambda connection, source_id, lock: connection.execute(text(
        'SELECT * FROM public.learning_resources WHERE id=:id'), {'id': source_id}).mappings().one_or_none()
    yield writer
    engine.dispose()


def snapshot(writer):
    with writer.engine.connect() as connection:
        row = writer._canonical_row(connection, 'RES001', lock=False)
        return dict(row)


def apply(writer, fields, expected):
    return writer.write_metadata(
        locator=SourceLocator(backend='postgresql', namespace='postgresql:learning',
                              entity_type='resources', source_record_id='RES001'),
        fields=fields, expected_source_state_hash=expected, idempotency_key='full-update-001')


def test_full_metadata_update_replaces_relations_and_replay_does_not_write_twice(full_writer):
    expected = state_hash(snapshot(full_writer))
    fields = {'title': 'New', 'provider_id': 'P2', 'resource_type_id': 'T2',
              'publication_status': 'PUBLISHED', 'subject_id': 'S2', 'grade_level_id': 'G2'}
    first = apply(full_writer, fields, expected)
    after = snapshot(full_writer)
    second = apply(full_writer, fields, expected)
    assert first['status'] == second['status'] == 'APPLIED'
    assert second['idempotent_replay']
    assert snapshot(full_writer) == after
    with full_writer.engine.connect() as connection:
        assert list(connection.execute(text('SELECT subject_id FROM public.resource_subjects')).scalars()) == ['S2']
        assert list(connection.execute(text('SELECT grade_level_id FROM public.resource_grade_levels')).scalars()) == ['G2']


def test_invalid_subject_grade_combination_rolls_back_every_field(full_writer):
    before = snapshot(full_writer)
    with pytest.raises(DomainError, match='SOURCE_WRITEBACK_SUBJECT_GRADE_MISMATCH'):
        apply(full_writer, {'title': 'Must roll back', 'subject_id': 'S2', 'grade_level_id': 'G1'}, state_hash(before))
    assert snapshot(full_writer) == before


def test_relation_removal_is_supported_and_stale_revision_is_rejected(full_writer):
    before = snapshot(full_writer)
    apply(full_writer, {'subject_id': None, 'grade_level_id': None}, state_hash(before))
    with full_writer.engine.connect() as connection:
        assert not list(connection.execute(text('SELECT * FROM public.resource_subjects')))
        assert not list(connection.execute(text('SELECT * FROM public.resource_grade_levels')))
    with pytest.raises(DomainError, match='SOURCE_WRITEBACK_CONFLICT'):
        apply(full_writer, {'title': 'Stale'}, state_hash(before))


def test_federated_master_ids_are_translated_before_source_cas():
    resource_locator = SourceLocator(backend='postgresql', namespace='school-a',
                                     entity_type='resources', source_record_id='RES001')
    user_locator = SourceLocator(backend='postgresql', namespace='school-a',
                                 entity_type='users', source_record_id='USR001')
    user = {'source_origin': 'POSTGRESQL', 'source_identity_version': 1,
            'source_backend': 'postgresql', 'source_namespace': 'school-a',
            'source_entity_type': 'users', 'source_record_key': 'USR001',
            'source_identity_hash': source_identity_hash(user_locator)}

    class Repo:
        def get(self, table, key):
            assert (table, key) == ('master_users', 'MASTER_USER')
            return user

    source = {'id': 'RES001', 'created_by': 'USR001', 'title': 'Title'}
    master = {**source, 'id': 'MASTER_RESOURCE', 'created_by': 'MASTER_USER'}
    master['source_state_hash'] = state_hash(master)
    assert master['source_state_hash'] != state_hash(source)
    assert canonical_source_hash(Repo(), master, resource_locator) == state_hash(source)

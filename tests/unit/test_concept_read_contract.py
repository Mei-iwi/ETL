"""Prompt 01: synthetic read capabilities; no live database or application startup."""
import json

import pytest

from data_sync_etl.adapters.concept_corpus import (
    CorpusQueryPlanGate,
    CorpusReadOnlyAccessGate,
    ReadOnlyMongoEvidenceReader,
)
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from tests.unit.test_dynamic_concept_dictionary import Cursor, resource


def reader(rows, *, collection='learning_resources', verifier=lambda: True, **budgets):
    cursor = Cursor(rows)
    calls = []

    def find(selector, **options):
        calls.append((selector, options))
        return cursor

    instance = ReadOnlyMongoEvidenceReader({collection: find}, CorpusReadOnlyAccessGate(verifier),
        query_gate=CorpusQueryPlanGate(lambda name: name == collection), **budgets)
    return instance, cursor, calls


def test_deny_unverified_grants_and_sanitize_attestor_failure():
    def unavailable():
        raise RuntimeError('sensitive operator evidence must not escape')

    for gate in (CorpusReadOnlyAccessGate(), CorpusReadOnlyAccessGate(unavailable)):
        with pytest.raises(ConceptBlocked, match='^MONGO_READ_ONLY_NOT_VERIFIED$'):
            gate.require()


def test_unindexed_path_degrades_to_blocked_before_secret_or_connection(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Missing query-plan evidence must block before any IO')

    with pytest.raises(ConceptBlocked, match='QUERY_PLAN_NOT_VERIFIED'):
        ReadOnlyMongoEvidenceReader.from_secret_reference(tmp_path / 'missing', 'fixture',
            CorpusReadOnlyAccessGate(lambda: True), client_factory=forbidden)
    instance = ReadOnlyMongoEvidenceReader({'learning_resources': forbidden}, CorpusReadOnlyAccessGate(lambda: True))
    with pytest.raises(ConceptBlocked, match='QUERY_PLAN_NOT_VERIFIED'):
        instance.metadata('m1')


@pytest.mark.parametrize('extra', [{'authors': ['private']}, {'email': 'private'}, {'embedding': [1]}])
def test_projection_no_sensitive_fields(extra):
    instance, cursor, calls = reader([{'_id': 'm1', 'description': 'public', **extra}])
    with pytest.raises(ConceptBlocked, match='PROJECTION_INVALID'):
        instance.metadata('m1')
    assert cursor.closed
    assert set(calls[0][1]['projection']) == {'_id', 'description', 'tags', 'updated_at'}


def test_reject_write_capable_operation_and_selector_injection():
    with pytest.raises(ConceptBlocked, match='CAPABILITY_INVALID'):
        ReadOnlyMongoEvidenceReader({'command': lambda: None}, CorpusReadOnlyAccessGate(lambda: True))
    instance, cursor, calls = reader([])
    with pytest.raises(ConceptBlocked, match='IDENTITY_INVALID'):
        instance.metadata({'$ne': None})
    assert not calls
    assert not any(hasattr(instance, name) for name in ('command', 'aggregate', 'insert', 'update', 'delete'))


def test_bounded_cursor_metadata_rechecks_gate_after_fetch_and_closes():
    checks = iter([True, True, False])  # constructor, before find, after next(cursor)
    instance, cursor, calls = reader([{'_id': 'm1', 'description': 'public'}], verifier=lambda: next(checks))
    with pytest.raises(ConceptBlocked, match='READ_ONLY_NOT_VERIFIED'):
        instance.metadata('m1')
    assert cursor.closed and cursor.cap == 2 and cursor.batch == 2
    assert calls[0][1]['max_time_ms'] == 1000


def test_bounded_cursor_does_not_trust_limit_or_unbounded_tags():
    instance, cursor, _ = reader([{'_id': 'm1'}] * 5)
    with pytest.raises(ConceptBlocked, match='IDENTITY_MISMATCH'):
        instance.metadata('m1')
    assert cursor.closed
    instance, cursor, _ = reader([{'_id': 'm1', 'tags': ['a' * 100]}], max_bytes=40)
    with pytest.raises(ConceptBlocked, match='BYTE_BUDGET'):
        instance.metadata('m1')
    assert cursor.closed


def test_private_resource_never_opens_cursor():
    instance, _, calls = reader([])
    assert list(instance.fields(resource(public=False, mongo_id='m1'))) == []
    assert not calls


def test_validate_contract_cli_never_connects_or_reads_settings(monkeypatch, capsys):
    import pymongo
    import sqlalchemy

    from scripts import audit_concept_corpus

    def forbidden(*args, **kwargs):
        pytest.fail('Offline contract validation must not read env or connect')

    monkeypatch.setattr(pymongo, 'MongoClient', forbidden)
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)
    monkeypatch.setattr(audit_concept_corpus, 'Settings', forbidden)
    assert audit_concept_corpus.main(['--validate-contract']) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'BLOCKED_LIVE'
    assert report['contract_status'] == 'OFFLINE_VERIFIED'
    assert report['database_connection_performed'] is False
    assert set(report['allowed_collections']) == {'learning_resources', 'mongo_content_units'}

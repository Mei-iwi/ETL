"""Offline operator lifecycle with no app startup, network or real credentials."""
import json
from dataclasses import asdict

from data_sync_etl.cli.concepts import main
from tests.unit.test_dynamic_concept_dictionary import resource


def test_cli_discover_review_build_validate_publish_rollback_and_live_block(tmp_path, capsys, monkeypatch):
    import pymongo
    import sqlalchemy
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline CLI attempted DB access')
    monkeypatch.setattr(pymongo, 'MongoClient', forbidden)
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)
    row = asdict(resource())
    row['approved_unit_ids'] = []
    source = tmp_path / 'synthetic.json'
    source.write_text(json.dumps({'scope': 'offline_fixture', 'resources': [row]}), encoding='utf-8')
    root = tmp_path / 'operator'
    common = ['--root', str(root), '--fixture', str(source)]
    def run(action, *extra):
        assert main([action, *common, *extra]) == 0
        return json.loads(capsys.readouterr().out)
    assert run('discover')['status'] == 'CANDIDATE_ONLY'
    candidate = run('candidates')['relations'][0]
    for kind, extra in [('term', ['--term-index', '0']), ('term', ['--term-index', '1']), ('relation', [])]:
        run('review', '--candidate-id', candidate['id'], '--kind', kind, '--decision', 'approved',
            '--reviewer', 'fixture-operator', *extra)
    manifest = run('build')
    assert manifest['count'] == 1 and run('status')['generation'] is None
    assert run('validate', '--generation', manifest['generation'])['count'] == 1
    assert run('publish', '--generation', manifest['generation'])['generation'] == manifest['generation']
    assert run('rollback', '--generation', manifest['generation'])['generation'] == manifest['generation']
    blocked = tmp_path / 'not-created'
    assert main(['discover', '--root', str(blocked), '--live']) == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'BLOCKED_LIVE'
    assert not blocked.exists()


def test_readonly_factory_never_reads_secret_before_verified_gate(tmp_path):
    from data_sync_etl.adapters.concept_corpus import (
        CorpusReadOnlyAccessGate,
        ReadOnlyMongoEvidenceReader,
    )
    from data_sync_etl.ports.concept_corpus import ConceptBlocked
    try:
        ReadOnlyMongoEvidenceReader.from_secret_reference(tmp_path / 'MISSING-secret', 'fixture', CorpusReadOnlyAccessGate())
    except ConceptBlocked as error:
        assert str(error) == 'MONGO_READ_ONLY_NOT_VERIFIED'
    else:
        raise AssertionError('Unverified secret was read')


def test_readonly_factory_has_small_pool_majority_and_no_generic_client(tmp_path):
    from data_sync_etl.adapters.concept_corpus import (
        CorpusQueryPlanGate,
        CorpusReadOnlyAccessGate,
        ReadOnlyMongoEvidenceReader,
    )
    from tests.unit.test_dynamic_concept_dictionary import Cursor
    secret = tmp_path / 'synthetic-uri'
    secret.write_text('mongodb://fixture.invalid', encoding='utf-8')
    seen = {}
    class Collection:
        def find(self, *args, **kwargs):
            return Cursor([])
    class Database:
        def __getitem__(self, name):
            assert name in ('learning_resources', 'mongo_content_units')
            return Collection()
    class Client:
        def get_database(self, name, **kwargs):
            seen['read_concern'] = kwargs['read_concern'].document
            return Database()
        def close(self):
            seen['closed'] = True
    def factory(uri, **kwargs):
        seen.update(kwargs)
        return Client()
    reader = ReadOnlyMongoEvidenceReader.from_secret_reference(secret, 'fixture',
        CorpusReadOnlyAccessGate(lambda: True), client_factory=factory,
        query_gate=CorpusQueryPlanGate(lambda name: name in ('learning_resources', 'mongo_content_units')))
    assert seen['maxPoolSize'] == 2 and seen['socketTimeoutMS'] == 3000
    assert seen['read_concern'] == {'level': 'majority'}
    assert not hasattr(reader, 'client') and not hasattr(reader, 'command')
    reader.close()
    assert seen['closed']

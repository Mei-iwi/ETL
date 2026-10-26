"""Gate zero: conservative privileges, local-only endpoints, no DB/model IO by default."""
import pytest

from data_sync_etl.config import Settings
from scripts.audit_concept_corpus import (
    local_mongo_audit,
    loopback_uri,
    privilege_gate,
    static_audit,
)


@pytest.mark.parametrize('uri,expected', [
    ('mongodb://127.0.0.1:27017', True), ('mongodb://localhost/test', True),
    ('mongodb://[::1]/test', True), ('postgresql+psycopg://localhost/test', True),
    ('mongodb+srv://localhost/test', False), ('mongodb://remote.invalid/test', False),
    ('mongodb://127.0.0.1.evil.invalid/test', False), ('mongodb://localhost,remote.invalid/test', False),
])
def test_only_local_endpoints(uri, expected):
    assert loopback_uri(uri) is expected


@pytest.mark.parametrize('actions,authenticated,expected', [
    (['find', 'listIndexes', 'listCollections'], True, True),
    (['find', 'insert'], True, False), (['find', 'update'], True, False),
    (['find', 'createIndex'], True, False), (['anyAction'], True, False),
    ([], False, False), (['find'], False, False), ([], True, False),
])
def test_roles_or_no_auth_never_imply_read_only(actions, authenticated, expected):
    gate = privilege_gate({'authInfo': {'authenticatedUsers': [{}] if authenticated else [],
        'authenticatedUserPrivileges': [{'actions': actions}]}})
    assert gate['read_only_verified'] is expected


def test_offline_audit_does_not_connect_or_change_seed(monkeypatch):
    import pymongo
    import sqlalchemy
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected database connection')
    monkeypatch.setattr(pymongo, 'MongoClient', forbidden)
    monkeypatch.setattr(sqlalchemy, 'create_engine', forbidden)
    settings = Settings(_env_file=None)
    report = static_audit(settings)
    assert report['seed_concepts'] > 0
    assert set(report['mongo_contracts']) == {'learning_resources', 'users', 'ocr_pages', 'mongo_content_units', 'content_bags'}
    assert report['database_writes'] is report['model_loaded_or_downloaded'] is False
    assert report['revocation_gate']['status'] == 'BLOCKED'


@pytest.mark.parametrize('uri', ['mongodb://127.0.0.1:27017', 'mongodb://remote.invalid/test'])
def test_mongo_gate_never_uses_admin_commands_or_connects_without_verified_credentials(monkeypatch, uri):
    import pymongo
    def forbidden(*args, **kwargs):
        raise AssertionError('No Mongo connections or admin commands permitted at this gate')
    monkeypatch.setattr(pymongo, 'MongoClient', forbidden)
    report = local_mongo_audit(Settings(_env_file=None, mongo_uri=uri))
    assert report['status'] in ('BLOCKED', 'NOT_RUN')

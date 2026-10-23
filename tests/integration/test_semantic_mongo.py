"""Explicit existing isolated Mongo DB only; never create/drop a database.

Collections/test generations are retained as evidence. No cleanup of live data.
"""
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import WriteError

from data_sync_etl.adapters.mongo.semantic import MongoSemanticStore, install
from data_sync_etl.application.semantic import SemanticService
from data_sync_etl.domain.indexing import IndexingError, digest
from tests.integration.test_semantic_model import real_model  # noqa: F401
from tests.unit.test_search import searchable  # noqa: F401


@pytest.fixture
def mongo_semantic():
    uri, name = os.environ.get('SEMANTIC_TEST_MONGO_URI'), os.environ.get('SEMANTIC_TEST_MONGO_DATABASE')
    if not uri or not name:
        pytest.skip('Existing isolated Mongo test DB not configured; no live database writes')
    parsed = urlsplit(uri)
    if parsed.scheme != 'mongodb' or parsed.hostname not in ('127.0.0.1', 'localhost') or not name.startswith('etl_test_'):
        pytest.fail('Refusing non-local/non-test Mongo target', pytrace=False)
    client = MongoClient(uri, serverSelectionTimeoutMS=3000, timeoutMS=10000)
    try:
        if name not in client.list_database_names():
            pytest.fail('Test database must already exist; refusing database creation', pytrace=False)
        database = client[name]
        if os.environ.get('SEMANTIC_TEST_ALLOW_INSTALL') == '1':
            install(database)
        store = MongoSemanticStore(database)
        store.ensure_ready()
        yield store
    finally:
        client.close()


def test_real_mongo_unique_lease_validator_and_no_legacy_writes(mongo_semantic):
    store = mongo_semantic
    plan = {'_id': digest(uuid4().hex), 'resource_version_id': 'test-version', 'chunk_count': 1}
    owner = store.claim(plan, 120)
    assert store.manifest(plan['_id'])['status'] == 'RUNNING'
    with pytest.raises(IndexingError, match='SEMANTIC_INDEX_BUSY'):
        store.claim(plan, 120)
    # The strict dimension validator must reject even a direct driver bypass.
    with pytest.raises(WriteError):
        store.vectors.insert_one({'_id': digest(uuid4().hex), 'vector': [1.] * 384})
    store.finish(plan['_id'], owner, 'FAILED', 'TEST_LEASE_RETRY')
    assert store.claim(plan, 120) != owner


def test_real_chunk_bge_mongo_retrieval_and_idempotent_reindex(
        mongo_semantic, real_model, searchable):  # noqa: F811
    c, version = searchable
    service = SemanticService(c.indexing, real_model, mongo_semantic, c.settings)
    result = service.build(version['id'], apply=True)
    assert result['status'] == 'READY'
    assert service.status(version['id'])['coverage_verified']
    assert service.build(version['id'], apply=True)['action'] == 'UNCHANGED'
    c.settings.semantic_enabled = True
    c.search.semantic = service
    response = c.search.search('native text extraction', method='vector')
    assert response['total'] == 1
    assert response['items'][0]['resource_version_id'] == version['id']

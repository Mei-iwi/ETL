"""Protocol/authority fixtures, not multilingual model-quality evidence."""
import math

import pytest

from data_sync_etl.adapters.live_metadata_embedding import LiveMetadataEmbedding
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_metadata_search import search

QUERY = 'How to add fractions with different denominators?'


class Embedding:
    def __init__(self):
        self.documents, self.queries = [], []
        self.revision = 'unit-title-v1'

    def model_identity(self):
        return {'model_id': 'BAAI/bge-m3', 'revision': self.revision, 'dimension': 1024,
                'representation': 'metadata-title-v1'}

    def embed_documents(self, texts):
        self.documents.extend(texts)
        return [{'vector': [1.] + [0.] * 1023} for _ in texts]

    def embed_queries(self, texts):
        self.queries.extend(texts)
        return [{'vector': [1.] + [0.] * 1023} for _ in texts]


def configure(metadata, monkeypatch):
    c, version, _, service = metadata
    c.settings.search_metadata_content_enabled = True
    c.settings.search_metadata_live_dense_fallback_enabled = True
    monkeypatch.setattr('data_sync_etl.application.search.monotonic', lambda: 0.)
    monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(
        IndexingError('METADATA_SCHEMA_NOT_READY')))
    monkeypatch.setattr(c.search, '_content_search', lambda *_a, **_k: (_ for _ in ()).throw(
        IndexingError('SEARCH_DATA_TIMEOUT', 503)))
    embedding = Embedding()
    service.live_embedding = embedding

    def forbidden(*_a, **_k):
        pytest.fail('Live title vectors touched stored index')

    for name in ('get_many', 'upsert', 'invalidate', 'page'):
        monkeypatch.setattr(service.store, name, forbidden)
    return c, version, service, embedding


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
def test_live_dense_uses_actual_title_and_original_query_without_persisting(metadata, monkeypatch, method):
    c, version, service, embedding = configure(metadata, monkeypatch)
    result = search(c, QUERY, method=method)
    assert result['total'] == 1
    item = result['items'][0]
    assert item['resource_version_id'] == version['id']
    assert item['match_origin'] == 'METADATA' and item['matched_fields'] == ['TITLE']
    assert item['context_url'] is None
    assert result['branch_status']['metadata_dense'] == 'READY'
    assert result['branch_status']['metadata_dense_representation'] == 'TITLE'
    assert result['branch_status']['content'] == 'SEARCH_DATA_TIMEOUT'
    with c.uow() as repo:
        assert embedding.documents == [repo.get('master_learning_resources', version['resource_id'])['title']]
    assert embedding.queries == [QUERY]
    assert service.store.rows == {}


def test_dense_fallback_never_prunes_a_corpus_to_its_cpu_limit(metadata, monkeypatch):
    c, _, _, embedding = configure(metadata, monkeypatch)
    c.settings.search_metadata_live_dense_max_resources = 1
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-2', {'source_origin': 'LOCAL'})
        repo.insert('resource_versions', {'resource_id': 'resource-2', 'version_number': 1,
            'lifecycle_status': 'INGESTED', 'storage_bucket': 'fixture', 'storage_object_key': 'second.pdf',
            'source_hash': 'b' * 64})
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c, QUERY, method='vector')
    assert embedding.documents == embedding.queries == []


def test_invalid_live_vector_cannot_become_a_semantic_hit(metadata, monkeypatch):
    c, _, _, embedding = configure(metadata, monkeypatch)
    embedding.embed_documents = lambda texts: [{'vector': [1., 0.]} for _ in texts]
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c, QUERY, method='vector')
    assert not embedding.queries


@pytest.mark.parametrize('score', [.55, .49])
def test_vector_returns_nearest_live_title_below_threshold(metadata, monkeypatch, score):
    c, _, _, embedding = configure(metadata, monkeypatch)
    c.settings.search_vector_min_similarity = .6
    c.settings.search_metadata_live_dense_min_similarity = .5
    embedding.embed_documents = lambda texts: [
        {'vector': [score, math.sqrt(1 - score * score)] + [0.] * 1022} for _ in texts]
    result = search(c, QUERY, method='vector')
    assert result['total'] == 1
    assert c.settings.search_vector_min_similarity == .6


def test_model_identity_change_during_live_encoding_fails_closed(metadata, monkeypatch):
    c, _, _, embedding = configure(metadata, monkeypatch)
    encode = embedding.embed_documents

    def changed(texts):
        rows = encode(texts)
        embedding.revision = 'changed'
        return rows

    embedding.embed_documents = changed
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c, QUERY, method='vector')
    assert not embedding.queries


def test_revocation_during_live_encoding_discards_the_semantic_result(metadata, monkeypatch):
    c, version, _, embedding = configure(metadata, monkeypatch)
    encode = embedding.embed_documents

    def revoked(texts):
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'publication_status': 'DRAFT'})
        return encode(texts)

    embedding.embed_documents = revoked
    access = SearchAccess('authenticated_user', owner_id='stranger', allow_public=True,
                          allow_shared_unreviewed=True)
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c, QUERY, method='vector', access=access)


def test_resident_adapter_validates_dimensions_and_does_not_cache_query_vectors():
    class Resident:
        dimension, model_id, version = 1024, 'BAAI/bge-m3', 'unit-only'

        def __init__(self):
            self.encoded, self.checked = [], []

        def require_search_ready(self):
            pass

        def validate_query(self, text):
            self.checked.append(text)

        def embed_many(self, texts):
            self.encoded.extend(texts)
            return [[1.] + [0.] * 1023 for _ in texts]

    runtime = Resident()
    adapter = LiveMetadataEmbedding(runtime)
    for _ in range(2):
        assert len(adapter.embed_queries([QUERY])[0]['vector']) == 1024
    assert runtime.checked == runtime.encoded == [QUERY, QUERY]
    assert adapter.model_identity()['representation'] == 'metadata-title-v1'
    runtime.dimension = 384
    with pytest.raises(IndexingError, match='INVALID_EMBEDDING'):
        adapter.embed_queries([QUERY])
    assert len(runtime.encoded) == 2

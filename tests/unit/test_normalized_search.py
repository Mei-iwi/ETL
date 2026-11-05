"""Security/protocol fixtures; real multilingual quality is tested separately."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.exact_text_vectors import ExactTextVectors
from data_sync_etl.adapters.mongo.foundation_store import MongoContentReader
from data_sync_etl.application.normalized_search import NormalizedContentSearch
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from tests.unit.test_mongo_foundation import leaf
from tests.unit.test_search import searchable as searchable


class Embedding:
    def __init__(self):
        self.runtime = SimpleNamespace(require_search_ready=lambda: None)
        self.revision, self.inputs, self.queries = 'fixture-v1', [], []

    def model_identity(self):
        return {'model_id': 'BAAI/bge-m3', 'revision': self.revision, 'dimension': 1024}

    def embed_documents(self, texts):
        self.inputs.extend(texts)
        return [{'vector': [1.] + [0.] * 1023} for _ in texts]

    def embed_queries(self, texts):
        self.queries.extend(texts)
        return [{'vector': [1.] + [0.] * 1023} for _ in texts]


def configure(searchable):
    c, version = searchable
    c.settings.search_metadata_enabled = True
    c.settings.search_metadata_content_enabled = True
    c.settings.search_normalized_shortlist_enabled = True
    embedding = Embedding()
    c.search.normalized_search = NormalizedContentSearch(c.search, ExactTextVectors(embedding))
    return c, version, embedding


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_reads_real_normalized_text_and_returns_verified_context_without_writes(searchable, method):
    c, version, embedding = configure(searchable)
    before = deepcopy((c.documents.units, c.indexing.bags.rows))
    result = c.search.search('page text', method)
    assert result['total'] == 1
    assert result['items'][0]['resource_version_id'] == version['id']
    assert result['items'][0]['context_url'].startswith('/api/v1/chunks/')
    assert result['branch_status']['content_representation'] == 'NORMALIZED_BGE_SHORTLIST'
    assert before == (c.documents.units, c.indexing.bags.rows)
    if method != 'bow':
        assert embedding.inputs and all('Page' in text for text in embedding.inputs)
        assert embedding.queries == ['page text']


def test_embeddings_reused_but_source_text_and_rights_are_not(searchable):
    c, version, embedding = configure(searchable)
    assert c.search.search('page text', 'vector')['total'] == 1
    previous = list(embedding.inputs)
    assert c.search.search('page text', 'vector')['total'] == 1
    assert embedding.inputs == previous
    with c.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
    assert c.search.search('page text', 'vector')['total'] == 0


def test_winner_bag_mutation_cannot_be_hidden_by_pure_vector_memoization(searchable):
    c, _, _ = configure(searchable)
    assert c.search.search('page text', 'vector')['total'] == 1
    next(iter(c.indexing.bags.rows.values())).embedding = [0., 0., 0.]
    result = c.search.search('page text', 'vector')
    assert result['total'] == 0 and result['branch_status']['content'] == 'SEARCH_INDEX_STALE'


def test_revocation_during_model_work_fails_closed(searchable):
    c, version, embedding = configure(searchable)
    original = embedding.embed_documents
    def revoked(texts):
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
        return original(texts)
    embedding.embed_documents = revoked
    result = c.search.search('page text', 'vector')
    assert result['total'] == 0 and result['branch_status']['content'] == 'SEARCH_INDEX_STALE'


def test_corpus_limit_rejects_instead_of_truncating_before_inference(searchable):
    c, _, embedding = configure(searchable)
    c.settings.search_normalized_max_chunks = 1
    with pytest.raises(SearchError, match='SEARCH_LIMIT_EXCEEDED'):
        c.search.search('page text', 'vector')
    assert not embedding.inputs


def test_exact_vectors_are_immutable_bounded_and_invalidated_by_model_or_text():
    embedding = Embedding()
    vectors = ExactTextVectors(embedding, capacity=2)
    first = vectors.encode(['alpha', 'alpha'])
    assert embedding.inputs == ['alpha']
    first[0][0] = 99.
    assert vectors.encode(['alpha'])[0][0] == 1.
    vectors.encode(['changed text'])
    embedding.revision = 'fixture-v2'
    vectors.encode(['alpha'])
    assert embedding.inputs == ['alpha', 'changed text', 'alpha']
    assert len(vectors._vectors) == 2
    assert all(len(key) == 64 and isinstance(value, tuple) for key, value in vectors._vectors.items())
    embedding.runtime.require_search_ready = lambda: (_ for _ in ()).throw(IndexingError('MODEL_DOWN'))
    with pytest.raises(IndexingError, match='MODEL_DOWN'):
        vectors.encode(['alpha'])


def test_invalid_generation_never_publishes_partial_vectors():
    embedding = Embedding()
    vectors = ExactTextVectors(embedding)
    original = embedding.embed_documents
    def changed(texts):
        rows = original(texts)
        embedding.revision = 'changed'
        return rows
    embedding.embed_documents = changed
    with pytest.raises(IndexingError, match='SEMANTIC_MODEL_IDENTITY_CHANGED'):
        vectors.encode(['alpha'])
    assert not vectors._vectors


def test_restart_memo_is_lossless_digest_bound_and_corruption_recomputes(tmp_path):
    path = tmp_path / 'vectors.bin'
    first = Embedding()
    cache = ExactTextVectors(first, capacity=2, cache_file=path)
    expected = cache.encode(['private source text'])
    assert b'private source text' not in path.read_bytes()
    second = Embedding()
    restarted = ExactTextVectors(second, capacity=2, cache_file=path)
    assert restarted.encode(['private source text']) == expected
    assert second.inputs == []
    restarted.encode(['changed source text'])
    assert second.inputs == ['changed source text']
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    recovered = ExactTextVectors(second, capacity=2, cache_file=path)
    assert recovered._vectors == {}
    recovered.encode(['private source text'])
    assert second.inputs[-1] == 'private source text'


def test_selected_root_still_requires_complete_index_proof(searchable):
    c, version, _ = configure(searchable)
    with c.uow() as repo:
        snapshot = c.indexing._snapshot(repo, version['id'])
        run = c.indexing._runs(repo, version['id'])[0]
    root = next(iter(c.indexing._roots(snapshot)))
    assert len(c.indexing._verified(snapshot, run, selected_roots=[root])) == 1
    for bad in [[], ['missing'], [root, root]]:
        with pytest.raises(IndexingError, match='INDEX_ARTIFACT_INVALID'):
            c.indexing._verified(snapshot, run, selected_roots=bad)
    c.indexing.bags.rows[root].embedding = [0., 0., 0.]
    with pytest.raises(IndexingError):
        c.indexing._verified(snapshot, run, selected_roots=[root])


def test_normalized_phase_is_fresh_strict_and_never_reads_bags():
    db = MagicMock()
    db.with_options.return_value = db
    rows = [leaf(content_unit_id='leaf-1').bson()]
    def packed(*_a, **_k):
        return [{'_id': {'text': row['text'], 'text_type': 'string', 'markdown': row['markdown'],
                         'markdown_type': 'string', 'same': False},
                 'records': [{key: value for key, value in row.items() if key not in ('text', 'markdown')}]}
                for row in deepcopy(rows)]
    db.mongo_content_units.aggregate.side_effect = packed
    reader = MongoContentReader(db)
    phase = reader.normalized_phase(['v-1'], max_chars=10000)
    assert phase('v-1')[0].id == 'leaf-1'
    rows[0]['text'] = 'fresh changed source'
    assert reader.normalized_phase(['v-1'], max_chars=10000)('v-1')[0].text == 'fresh changed source'
    assert not db.content_bags.find.called
    rows[0]['is_private'] = True
    with pytest.raises(DomainError, match='Invalid Mongo document'):
        reader.normalized_phase(['v-1'], max_chars=10000)

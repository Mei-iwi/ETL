import math
from copy import deepcopy
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
from data_sync_etl.adapters.mongo.semantic import contracts
from data_sync_etl.application.semantic import SemanticService
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.semantic import context_headings, embedding_input, normalize_dense
from tests.semantic_support import MemorySemanticStore, SemanticModelDouble
from tests.unit.test_search import searchable  # noqa: F401


@pytest.fixture
def semantic_search(searchable):  # noqa: F811
    c, version = searchable
    model, store = SemanticModelDouble(), MemorySemanticStore()
    service = SemanticService(c.indexing, model, store, c.settings)
    c.search.semantic = service
    return c, version, service


def test_verified_context_unicode_missing_long_metadata_and_raw_unchanged():
    chunk = {'text': 'Quy đồng mẫu số, rồi cộng hai phân số. x² + y = 3'}
    before = deepcopy(chunk)
    assert embedding_input(chunk, strategy='raw') == chunk['text']
    assert embedding_input(chunk) == 'Content: ' + chunk['text']
    enriched = embedding_input(chunk, title='Toán 6', headings=['Phân số', 'Phép cộng'])
    assert enriched == 'Title: Toán 6\nHeading: Phân số > Phép cộng\nContent: ' + chunk['text']
    assert len(embedding_input(chunk, title='x' * 10000)) < 400
    assert chunk == before
    with pytest.raises(IndexingError, match='SEMANTIC_EMPTY_CHUNK'):
        embedding_input({'text': ' \n '})


def test_heading_path_rejects_cycles():
    units = [{'id': 'a', 'parent_unit_id': None, 'title': 'Root'},
             {'id': 'b', 'parent_unit_id': 'a', 'title': 'Child'}]
    assert context_headings(units, 'b') == ['Root', 'Child']
    units[0]['parent_unit_id'] = 'b'
    with pytest.raises(IndexingError):
        context_headings(units, 'b')


@pytest.mark.parametrize('bad', [[0.] * 1024, [1.] * 384, [math.nan] * 1024,
                                     [math.inf] * 1024, [True] * 1024, []])
def test_invalid_dense(bad):
    with pytest.raises(IndexingError):
        normalize_dense(bad)


def test_normalization_handles_large_finite_vectors():
    result = normalize_dense([1e300] * 1024)
    assert len(result) == 1024
    assert math.hypot(*result) == pytest.approx(1.)


def test_validated_vectors_reused_without_mutating_store_or_relaxing_contract():
    spec={'chunk_id':'c','dimension':1024}
    raw={'chunk_id':'c','dimension':1024,'status':'READY','vector':[1.+1e-7]+[0.]*1023}
    before=deepcopy(raw)
    actual=SemanticService._validated([raw],[spec])
    assert actual['c']['vector'][0]==1.
    assert raw==before
    with pytest.raises(IndexingError,match='INVALID_EMBEDDING'):
        SemanticService._validated([{**raw,'vector':[1.1]+[0.]*1023}],[spec])
    with pytest.raises(IndexingError):
        SemanticService._validated([{**raw,'vector':[1.]+[0.]*383}],[spec])


def test_offline_model_missing_never_downloads(tmp_path):
    model = BgeM3Embedding(tmp_path, 'a' * 40)
    assert not model.is_available()
    with pytest.raises(IndexingError, match='SEMANTIC_MODEL_UNAVAILABLE'):
        model.prepare()


def test_adapter_batch_token_limit_and_revision_change(monkeypatch):
    model = BgeM3Embedding('.', 'a' * 40, max_tokens=64)
    class Array(list):
        def tolist(self):
            return list(self)
    class Tokenizer:
        def encode(self, text, **kwargs):
            return text.split()
    class Runtime:
        tokenizer = Tokenizer()
        def encode(self, texts, **kwargs):
            assert kwargs['return_sparse'] is False
            return {'dense_vecs': Array([[2.] + [0.] * 1023 for _ in texts])}
    model._model = Runtime()
    model._loaded_identity = model.model_identity()
    monkeypatch.setattr(model, 'is_available', lambda: True)
    result = model.embed_documents(['Tiếng Việt', 'English'])
    assert len(result) == 2 and result[0]['vector'][0] == 1.
    with pytest.raises(IndexingError, match='SEMANTIC_TOKEN_LIMIT'):
        model.embed_documents(['word ' * 100])
    with pytest.raises(IndexingError, match='SEMANTIC_BATCH_LIMIT'):
        model.embed_documents(['one'] * 3)
    model.model_revision = 'b' * 40
    with pytest.raises(IndexingError, match='SEMANTIC_MODEL_IDENTITY_CHANGED'):
        model.embed_queries(['query'])


def test_additive_schema_does_not_include_legacy():
    definitions = contracts()
    assert set(definitions) == {'semantic_chunk_vectors_v1', 'semantic_chunk_indexes_v1'}
    assert definitions['semantic_chunk_vectors_v1']['$jsonSchema']['properties']['vector']['minItems'] == 1024


def test_dry_run_batch_idempotence_preserves_legacy(semantic_search):
    c, version, service = semantic_search
    before = deepcopy(c.indexing.bags.rows), deepcopy(c.documents.units)
    assert service.build(version['id'])['status'] == 'DRY_RUN'
    assert not service.store.rows and not service.embedding.inputs
    result = service.build(version['id'], apply=True)
    assert result['status'] == 'READY'
    assert len(service.store.rows) == result['chunk_count']
    assert all('Content: ' in text for text in service.embedding.inputs)
    calls = service.embedding.calls
    assert service.build(version['id'], apply=True)['action'] == 'UNCHANGED'
    assert service.embedding.calls == calls
    assert before == (c.indexing.bags.rows, c.documents.units)


def test_failed_batch_resume_only_missing_records(semantic_search):
    c, version, service = semantic_search
    service.embedding.batch_size = 1
    service.embedding.fail_after = 1
    with pytest.raises(IndexingError, match='SEMANTIC_INFERENCE_FAILED'):
        service.build(version['id'], apply=True)
    assert len(service.store.rows) == 1
    assert next(iter(service.store.manifests.values()))['status'] == 'FAILED'
    service.embedding.fail_after = None
    calls = service.embedding.calls
    result = service.build(version['id'], apply=True)
    assert service.embedding.calls - calls == result['chunk_count'] - 1
    assert result['status'] == 'READY'


def test_title_and_strategy_have_independent_immutable_generations(semantic_search):
    c, version, service = semantic_search
    old = service.build(version['id'], apply=True)
    old_rows = deepcopy(service.store.rows)
    with c.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'title': 'Changed title'})
    new = service.build(version['id'], apply=True)
    assert old['index_id'] != new['index_id']
    assert all(service.store.rows[key] == value for key, value in old_rows.items())
    c.settings.semantic_strategy = 'raw'
    raw = service.build(version['id'], apply=True)
    assert raw['index_id'] not in (old['index_id'], new['index_id'])
    assert len(service.store.manifests) == 3


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
def test_search_chunk_context_hybrid_and_bow_unchanged(semantic_search, method):
    c, version, service = semantic_search
    before = c.search.search('learning', method='bow')
    service.build(version['id'], apply=True)
    c.settings.semantic_enabled = True
    result = c.search.search('completelydifferentwords', method=method)
    assert result['total'] == 1
    assert result['items'][0]['resource_id'] == version['resource_id']
    assert 'vector' in result['items'][0]['score_components']
    assert 'bge-m3-chunk-contextual' in result['ranking_algorithm']
    assert result['vector_index']['dimension'] == 1024
    assert result['vector_index']['index_ids']
    context = c.search.context(result['items'][0]['context_id'])
    assert any(row['selected'] for row in context['chunks'])
    assert c.search.search('learning', method='bow') == before


def test_missing_index_fails_closed_no_legacy_fallback(semantic_search):
    c, version, service = semantic_search
    before = c.search.search('learning', method='bow')
    c.settings.semantic_enabled = True
    with pytest.raises(IndexingError, match='SEMANTIC_INDEX_NOT_READY'):
        c.search.search('learning', method='vector')
    assert c.search.search('learning', method='bow') == before


def test_corrupted_vector_and_coverage_rejected(semantic_search):
    c, version, service = semantic_search
    service.build(version['id'], apply=True)
    row = next(iter(service.store.rows.values()))
    row['vector'] = [1.] * 384
    with pytest.raises(IndexingError, match='INVALID_EMBEDDING'):
        service.build(version['id'], apply=True)
    service.store.rows.pop(row['_id'])
    with pytest.raises(IndexingError, match='SEMANTIC_INDEX_INCOMPLETE'):
        service.build(version['id'], apply=True)


def test_authorization_filter_precedes_scoring_and_reranking(semantic_search):
    c, version, service = semantic_search
    service.build(version['id'], apply=True)
    snapshot, title = service._snapshot(version['id'])
    roots = c.indexing._roots(snapshot)
    vector = [1.] + [0.] * 1023
    assert service.scores(snapshot, title, {}, set(), vector, lambda: None, [0]) == {}
    allowed = {unit['id'] for unit in snapshot[2]}
    assert service.scores(snapshot, title, roots, allowed, vector, lambda: None, [0])
    with pytest.raises(IndexingError, match='SEMANTIC_CANDIDATE_LIMIT'):
        service.scores(snapshot, title, roots, allowed, vector, lambda: None,
                       [c.settings.semantic_max_candidates])


def test_rerank_is_bounded_and_only_sees_selected_chunks():
    settings = SimpleNamespace(semantic_rerank_top_k=2)
    model = SemanticModelDouble()
    service = SemanticService(None, model, None, settings)
    candidates = {'a': .9, 'b': .8, 'c': .7}
    bags = {key: (None, {'text': key}) for key in candidates}
    assert service.rerank('query', candidates, bags, lambda: None) == {'a': 1., 'b': 2.}


def test_rerank_diversifies_versions_before_candidate_cutoff():
    model = SemanticModelDouble()
    service = SemanticService(None, model, None, SimpleNamespace(semantic_rerank_top_k=2))
    scores = {'a1': .9, 'a2': .89, 'b1': .88}
    bags = {key: (SimpleNamespace(resource_version_id='A' if key.startswith('a') else 'B'),
                  {'text': key}) for key in scores}
    assert set(service.rerank('query', scores, bags, lambda: None)) == {'a1', 'b1'}


def test_inaccessible_version_does_not_require_a_semantic_index(semantic_search):
    c, version, service = semantic_search
    snapshot, metadata = service._snapshot(version['id'])
    assert not service.store.manifests
    assert service.scores(snapshot, metadata, {}, set(), [1.] + [0.] * 1023, lambda: None, [0]) == {}


def test_metadata_changes_during_encoding_never_publish_ready(semantic_search):
    c, version, service = semantic_search
    encode = service.embedding.embed_documents
    def changed(texts):
        output = encode(texts)
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'title': 'Concurrent change'})
        return output
    service.embedding.embed_documents = changed
    with pytest.raises(IndexingError, match='SEMANTIC_INDEX_STALE'):
        service.build(version['id'], apply=True)
    assert all(row['status'] == 'FAILED' for row in service.store.manifests.values())


def test_semantic_recheck_rejects_revocation_during_rerank(semantic_search):
    c, version, service = semantic_search
    service.build(version['id'], apply=True)
    c.settings.semantic_enabled = c.settings.semantic_rerank_enabled = True
    def revoke(query, texts):
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
        return [1.] * len(texts)
    service.embedding.rerank = revoke
    with pytest.raises(Exception, match='SEARCH_INDEX_STALE'):
        c.search.search('semantic description', method='vector')


def test_semantic_evaluation_records_actual_model_strategy_and_index(semantic_search):
    from data_sync_etl.application.evaluation import EvaluationService
    from data_sync_etl.domain.search_access import SearchAccess
    c, version, service = semantic_search
    service.build(version['id'], apply=True)
    c.settings.semantic_enabled = True
    evaluation = EvaluationService(c.search, None, c.settings)
    snapshot, fingerprint = evaluation.corpus({}, SearchAccess('local_demo'))
    assert snapshot['model']['id'] == 'TEST_DOUBLE_NOT_REAL_BGE'
    assert snapshot['semantic']['strategy'] == 'contextual'
    assert snapshot['versions'][0]['semantic_index_id']
    c.settings.semantic_rerank_enabled = True
    assert evaluation.corpus({}, SearchAccess('local_demo'))[1] != fingerprint


def test_semantic_http_response_and_context_keep_frontend_contract(semantic_search):
    from fastapi.testclient import TestClient

    from data_sync_etl.main import create_app
    c, version, service = semantic_search
    service.build(version['id'], apply=True)
    c.settings.semantic_enabled = True
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        result = api.post('/api/v1/search', json={'query': 'semantic description', 'method': 'hybrid'})
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['representation_unit'] == 'content_bag'
        assert body['vector_index']['dimension'] == 1024
        assert body['items'][0]['resource_version_id'] == version['id']
        context = api.get(body['items'][0]['context_url'])
        assert context.status_code == 200, context.text


def test_colbert_maxsim_not_cross_encoder(monkeypatch):
    import numpy as np
    model = BgeM3Embedding('.', 'a' * 40)
    query_tokens = np.array([[1., 0.], [0., 1.]])
    docs = {'query': query_tokens,
            'same': np.array([[1., 0.], [0., 1.]]),
            'opposite': np.array([[-1., 0.], [0., -1.]])}
    monkeypatch.setattr(model, '_encode', lambda texts, **kwargs: [{'colbert': docs[text]} for text in texts])
    assert model.rerank('query', ['same', 'opposite']) == [1., 0.]


def test_readonly_status_never_loads_or_writes_model(semantic_search):
    c, version, service = semantic_search
    assert service.status(version['id'])['status'] == 'NOT_STARTED'
    assert not service.embedding.inputs and not service.store.rows
    service.build(version['id'], apply=True)
    before = service.embedding.calls
    status = service.status(version['id'])
    assert status['coverage_verified']
    assert status['expected_chunks'] == status['persisted_chunks']
    assert service.embedding.calls == before


def test_curriculum_metadata_change_invalidates_semantic_without_changing_bow(semantic_search):
    c, version, service = semantic_search
    built = service.build(version['id'], apply=True)
    before = c.search.search('learning', method='bow')
    with c.uow() as repo:
        subject = repo.find('resource_subjects', {'resource_id': version['resource_id']})[0]
        repo.update('subjects', subject['subject_id'], {'subject_name': 'New verified subject name'})
    c.settings.semantic_enabled = True
    with pytest.raises(IndexingError, match='SEMANTIC_INDEX_STALE'):
        c.search.search('semantic description', method='vector')
    assert c.search.search('learning', method='bow') == before
    assert service.build(version['id'], apply=True)['index_id'] != built['index_id']
    assert any('Subject: New verified subject name' in text for text in service.embedding.inputs)


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
def test_chunk_semantic_coexists_with_meaning_bags_and_legacy_threshold(semantic_search, monkeypatch, method):
    """Merge wiring regression only: synthetic scores do not claim model quality."""
    c, version, service = semantic_search
    c.settings.index_meaning_bags_enabled = True
    c.indexing.enqueue(version['id'], force=True)
    assert c.indexing.once()
    service.build(version['id'], apply=True)
    c.settings.semantic_enabled = True
    c.settings.search_vector_min_similarity = .99
    original = service.scores
    def low_semantic_score(*args):
        return {key: (.2, chunk) for key, (_, chunk) in original(*args).items()}
    monkeypatch.setattr(service, 'scores', low_semantic_score)
    def wrong_branch(*_):
        pytest.fail('Chunk semantic must not score legacy vectors or meaning bags')
    monkeypatch.setattr('data_sync_etl.application.search.cosine', wrong_branch)
    monkeypatch.setattr('data_sync_etl.application.search.match_meaning', wrong_branch)
    result = c.search.search('completelydifferentwords', method=method)
    assert result['total'] == 1
    item = result['items'][0]
    assert item['score_components']['vector'] == .2
    assert item['context_evidence'] == 'semantic'
    assert item['meaning_variants'] == []
    assert result['query_expansion']['dictionary_fingerprint']
    assert result['vector_index']['dimension'] == 1024
    assert result['ranking_algorithm'].startswith('bge-m3-chunk-')


def test_search_recheck_skips_semantic_manifest_for_denied_version(semantic_search, monkeypatch):
    c, _, service = semantic_search
    c.settings.semantic_enabled = True
    monkeypatch.setattr(c.search, '_allowed_units', lambda *_: set())
    assert not service.store.manifests
    result = c.search.search('semantic description', method='vector')
    assert result['total'] == 0
    assert result['items'] == []

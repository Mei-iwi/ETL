"""Runtime/concurrency doubles are confined to tests; no model downloads."""
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.application.search import QueryEmbeddingContext
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search_access import SearchAccess
from tests.indexing_support import ModelDouble
from tests.unit.test_search import searchable as searchable

VIETNAMESE = (
    'học liệu', 'tiếng Việt', 'phân số', 'định luật', 'lịch sử', 'toán học',
    'cộng trừ', 'nhân chia', 'địa lý', 'sinh học', 'hóa học', 'vật lý',
    'lớp một', 'LỚP HAI', '  học liệu  ', 'Việ̂t', 'Vie\u0323̂t', 'a\u0301',
    'á', 'Đặng', 'Nguyễn', 'trường học 🏫', 'dòng\nmới', 'tri thức',
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    (tmp_path / 'modules.json').write_text('[{"path":""}]', encoding='utf-8')
    (tmp_path / 'etl-model-revision.txt').write_text('revision-1', encoding='utf-8')
    (tmp_path / 'model.safetensors').write_bytes(b'test double artifact only')
    monkeypatch.setattr('data_sync_etl.adapters.embeddings.find_spec', lambda _: True)
    calls = {'load': 0, 'encode': 0}

    def encode(text, **kwargs):
        assert isinstance(text, str) and kwargs == {'show_progress_bar': False}
        calls['encode'] += 1
        return SimpleNamespace(tolist=lambda: [0.1] * 384)

    def construct(path, **kwargs):
        assert path == str(tmp_path.resolve())
        assert kwargs == {'local_files_only': True, 'device': 'cpu'}
        calls['load'] += 1
        return SimpleNamespace(get_sentence_embedding_dimension=lambda: 384, encode=encode)

    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=construct))
    return LocalSentenceEmbedding(tmp_path, 'test-only', 'revision-1', 384), calls


def test_concurrent_cold_load_once_and_direct_embed_remains_safe(runtime):
    model, calls = runtime
    assert calls == {'load': 0, 'encode': 0}
    with ThreadPoolExecutor(max_workers=8) as pool:
        vectors = list(pool.map(model.embed, VIETNAMESE))
    assert calls == {'load': 1, 'encode': len(VIETNAMESE) + 1}  # one probe
    assert all(len(vector) == 384 for vector in vectors)
    assert model.ready()


def test_prepared_query_avoids_second_prepare_and_does_not_store_queries(runtime, monkeypatch):
    model, calls = runtime
    count = []
    original = model.prepare
    monkeypatch.setattr(model, 'prepare', lambda: (count.append(1), original())[-1])
    for query in VIETNAMESE:
        assert len(model.prepare_query()(query)) == 384
    assert len(count) == len(VIETNAMESE)
    assert calls['load'] == 1
    assert not any(query in repr(model.__dict__) for query in VIETNAMESE)


def test_query_token_budget_rejects_before_sentence_transformer_can_truncate(runtime):
    model, calls = runtime
    model.prepare()
    model._model.tokenizer = SimpleNamespace(encode=lambda text, **kwargs: text.split())
    model._model.max_seq_length = 4
    before = calls['encode']
    model.validate_query('C++ và C#')
    with pytest.raises(IndexingError, match='SEMANTIC_TOKEN_LIMIT'):
        model.validate_query('one two three four tail-condition')
    assert calls['encode'] == before


@pytest.mark.parametrize('change', ['missing_weights', 'bad_json', 'revision', 'dimension', 'id'])
def test_cached_vector_never_hides_unavailable_or_changed_runtime(runtime, change):
    model, _ = runtime
    with QueryEmbeddingContext() as context:
        context.encode(model, model.prepare_query(), 'học liệu', 384)
        if change == 'missing_weights':
            (model.path / 'model.safetensors').unlink()
        elif change == 'bad_json':
            (model.path / 'modules.json').write_text('{', encoding='utf-8')
        elif change == 'revision':
            model.version = 'revision-2'
            (model.path / 'etl-model-revision.txt').write_text(model.version, encoding='utf-8')
        elif change == 'dimension':
            model.dimension = 3
        else:
            model.model_id = 'changed-model'
        with pytest.raises(IndexingError, match='BLOCKED_EMBEDDING_RUNTIME'):
            context.encode(model, model.prepare_query(), 'học liệu', 384)
        assert not model.ready()
    assert context._vector is None


def test_initialization_error_is_retryable(runtime, monkeypatch):
    model, calls = runtime
    constructor = sys.modules['sentence_transformers'].SentenceTransformer
    monkeypatch.setattr(sys.modules['sentence_transformers'], 'SentenceTransformer',
                        lambda *args, **kw: (_ for _ in ()).throw(RuntimeError('private path')))
    with pytest.raises(IndexingError, match='BLOCKED_EMBEDDING_RUNTIME') as exc:
        model.embed('học liệu')
    assert 'private path' not in str(exc.value)
    assert not model.ready()
    monkeypatch.setattr(sys.modules['sentence_transformers'], 'SentenceTransformer', constructor)
    assert len(model.embed('học liệu')) == 384 and calls['load'] == 1


def test_context_exact_key_copy_invalidation_and_lifetime():
    model = ModelDouble()
    count = []
    def encode(query):
        count.append(query)
        return [.1, .2, .3]
    with QueryEmbeddingContext() as context:
        first = context.encode(model, encode, 'học liệu', 3)
        first[0] = 99
        assert context.encode(model, encode, 'học liệu', 3) == [.1, .2, .3]
        context.encode(model, encode, ' học liệu ', 3)  # exact text only here
        model.version = 'changed'
        context.encode(model, encode, ' học liệu ', 3)
        assert len(count) == 3
        assert 'học liệu' not in repr(context.__dict__)
        other = ModelDouble()
        context.encode(other, encode, ' học liệu ', 3)
        assert len(count) == 4
    assert context.__dict__ == {'_owner': None, '_key': None, '_vector': None}


@pytest.mark.parametrize('vector', [[float('nan')] * 3, [float('inf')] * 3, [1., 2.], [0., 0., 0.]])
def test_invalid_vector_is_never_retained(vector):
    with QueryEmbeddingContext() as context:
        with pytest.raises(IndexingError):
            context.encode(ModelDouble(), lambda _: vector, 'query', 3)
        assert context._vector is None


def test_search_reuse_preserves_ranking_permissions_and_bow_independence(searchable, monkeypatch):
    c, _ = searchable
    model = c.indexing.embedding
    calls = []
    original = model.embed
    monkeypatch.setattr(model, 'embed', lambda query: (calls.append(query), original(query))[-1])
    expected = {method: c.search.search('page', method) for method in ('vector', 'hybrid')}
    calls.clear()
    with QueryEmbeddingContext() as context:
        for method in ('vector', 'hybrid'):
            assert c.search.search('page', method, _query_context=context) == expected[method]
        assert calls == ['page']
        assert c.search.search('page', 'vector', access=SearchAccess(), _query_context=context)['items'] == []
        model.enabled = False
        assert c.search.search('page', 'bow', _query_context=context)['total'] >= 0
        with pytest.raises(IndexingError, match='BLOCKED_EMBEDDING_RUNTIME'):
            c.search.search('page', 'hybrid', _query_context=context)
    assert context._vector is None

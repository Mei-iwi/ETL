import gc
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from data_sync_etl.application.http_query_cache import HttpQueryVectorCache
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import normalize_query
from tests.search_query_cache_support import Runtime384


def encode(cache, model, query='query', *, commit=True, scope=None):
    with cache.request(**({'scope': scope} if scope else {})) as request:
        value = request.encode(model, model.prepare_query(), normalize_query(query), 384)
        if commit:
            request.commit()
        return value


def test_hit_copy_normalization_cross_query_and_no_raw_key():
    model = Runtime384()
    cache = HttpQueryVectorCache(model)
    first = encode(cache, model, '  Vie\u0323\u0302t  ')
    first[0] = 999
    assert encode(cache, model, 'Vi\u1ec7t')[0] != 999
    assert model.encodes == 1 and model.prepares == 2
    encode(cache, model, 'different')
    assert model.encodes == 2
    assert all(isinstance(key, bytes) and len(key) == 32 for key in cache._entries)
    assert 'different' not in repr(cache.__dict__)
    assert cache.stats()['hit'] == 1


def test_ttl_monotonic_hit_does_not_extend_and_staging():
    clock = [0.]
    model = Runtime384()
    cache = HttpQueryVectorCache(model, clock=lambda: clock[0])
    encode(cache, model, commit=False)
    assert cache.stats()['entries'] == 0
    encode(cache, model)
    clock[0] = 59.
    encode(cache, model)
    clock[0] = 60.
    encode(cache, model)
    assert model.encodes == 3 and cache.stats()['expired'] == 1


def test_lru_caps_soak_and_small_byte_budget():
    model = Runtime384()
    cache = HttpQueryVectorCache(model, max_entries=2)
    encode(cache, model, 'a')
    encode(cache, model, 'b')
    encode(cache, model, 'a')
    encode(cache, model, 'c')
    assert cache.stats()['entries'] == 2
    before = model.encodes
    encode(cache, model, 'b')
    assert model.encodes == before + 1
    full = HttpQueryVectorCache(model)
    for number in range(500):
        encode(full, model, str(number))
        assert full.stats()['entries'] <= 64
        assert full.stats()['accounted_bytes'] <= 1024 * 1024
    tiny = HttpQueryVectorCache(model, max_bytes=8192)
    encode(tiny, model)
    assert tiny.stats()['entries'] == 0 and tiny.stats()['bypass_limits'] == 1


@pytest.mark.parametrize('change', ['revision', 'dimension', 'owner', 'encoder', 'loaded_encoder', 'runtime', 'scope', 'query_contract'])
def test_unknown_or_changed_contract_bypasses(change, monkeypatch):
    model = Runtime384()
    cache = HttpQueryVectorCache(model)
    encode(cache, model)
    if change == 'revision':
        model.version = 'new'
    elif change == 'dimension':
        model.dimension = 383
    elif change == 'owner':
        model = Runtime384()
    elif change == 'encoder':
        original = Runtime384._encode
        monkeypatch.setattr(Runtime384, '_encode', lambda self, text: original(self, text))
    elif change == 'loaded_encoder':
        from tests.search_query_cache_support import EncoderIdentity

        model._model = EncoderIdentity()
    elif change == 'runtime':
        model.enabled = False
    elif change == 'query_contract':
        monkeypatch.setattr('data_sync_etl.application.http_query_cache.QUERY_CONTRACT', 'next-version')
    if change in ('runtime', 'dimension'):
        with pytest.raises(IndexingError):
            encode(cache, model)
    else:
        encode(cache, model, scope='unverified' if change == 'scope' else None)
    assert cache.stats()['hit'] == 0


@pytest.mark.parametrize('bad', [[0.] * 384, [float('nan')] * 384, [float('inf')] * 384, [True] * 384, [1.]])
def test_invalid_vectors_never_published(bad, monkeypatch):
    monkeypatch.setattr(Runtime384, '_encode', lambda self, text: bad)
    model = Runtime384()
    cache = HttpQueryVectorCache(model)
    with pytest.raises(IndexingError):
        encode(cache, model)
    assert cache.stats()['entries'] == 0


@pytest.mark.parametrize('failure', [RuntimeError, TimeoutError, KeyboardInterrupt])
def test_unsuccessful_request_discards_staged_vector(failure):
    model = Runtime384()
    cache = HttpQueryVectorCache(model)
    with pytest.raises(failure), cache.request() as request:
        request.encode(model, model.prepare_query(), 'query', 384)
        raise failure()
    assert cache.stats()['entries'] == 0 and cache.stats()['failed'] == 1


def test_concurrent_bounded_cache():
    model = Runtime384()
    cache = HttpQueryVectorCache(model)
    with ThreadPoolExecutor(max_workers=10) as pool:
        values = list(pool.map(lambda number: encode(cache, model, str(number % 5)), range(200)))
    assert all(len(value) == 384 for value in values)
    assert cache.stats()['entries'] <= 5 and cache.stats()['accounted_bytes'] < cache.max_bytes


def test_no_strong_owner_reference():
    model = Runtime384()
    cache = HttpQueryVectorCache(model)
    encode(cache, model)
    owner = weakref.ref(model)
    del model
    gc.collect()
    assert owner() is None


@pytest.mark.parametrize('options', [{'ttl': 61}, {'max_entries': 65}, {'max_bytes': 1024 * 1024 + 1}])
def test_invalid_bounds_rejected(options):
    with pytest.raises(ValueError):
        HttpQueryVectorCache(Runtime384(), **options)


def test_unweakrefable_or_unknown_runtime_cannot_break_uncached_startup():
    from types import SimpleNamespace

    runtime = SimpleNamespace(model_id='unknown', version='v1', dimension=384,
                              ready=lambda: True, _encode=lambda _: [0.1] * 384)
    cache = HttpQueryVectorCache(runtime)
    with cache.request() as request:
        assert len(request.encode(runtime, runtime._encode, 'query', 384)) == 384
        request.commit()
    assert cache.stats()['entries'] == 0 and cache.stats()['bypass_runtime'] == 1
    assert HttpQueryVectorCache(object()).stats()['entries'] == 0

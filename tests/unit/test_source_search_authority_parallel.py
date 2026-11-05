from contextvars import ContextVar
from threading import Barrier, Lock
from types import MethodType, SimpleNamespace

import pytest

from data_sync_etl.application.source_file_resolver import SourceFileResolver
from data_sync_etl.domain.core import SourceFileError
from data_sync_etl.domain.search_access import SearchAccess


def resolver(workers=4):
    return SourceFileResolver(None, None, None, None, None,
                              SimpleNamespace(source_search_authorization_workers=workers), None)


def test_parallel_checks_preserve_context_and_recheck_revocations_between_phases():
    value = resolver()
    scope = ContextVar('authority_test_scope')
    token = scope.set('request-only-context')
    barrier, lock = Barrier(4, timeout=5), Lock()
    active = peak = calls = 0

    def check(_self, version, resource, rows, access, *, source):
        nonlocal active, peak, calls
        assert scope.get() == 'request-only-context'
        assert access.actor_id == 'approved-fixture-actor'
        with lock:
            active += 1
            peak = max(peak, active)
            calls += 1
        try:
            barrier.wait()
            return source['active']
        finally:
            with lock:
                active -= 1

    value._derived_index_authorized = MethodType(check, value)
    ids = [str(i) for i in range(4)]
    authorities = {key: ({'id': key}, {}, []) for key in ids}
    live = {key: {'active': True} for key in ids}
    access = SearchAccess('authenticated_admin', actor_id='approved-fixture-actor')
    try:
        assert value._authorize_source_batch(ids, authorities, live, access) == dict.fromkeys(ids, True)
        live['0']['active'] = False
        assert value._authorize_source_batch(ids, authorities, live, access) == {'0': False, '1': True, '2': True, '3': True}
        assert peak == 4 and calls == 8
    finally:
        scope.reset(token)
        value.close()


def test_failed_parallel_check_never_returns_a_partial_authorization_map():
    value = resolver(2)
    def check(_self, version, resource, rows, access, *, source):
        if source == 'fail':
            raise SourceFileError('SOURCE_UNAVAILABLE')
        return True
    value._derived_index_authorized = MethodType(check, value)
    try:
        with pytest.raises(SourceFileError, match='SOURCE_UNAVAILABLE'):
            value._authorize_source_batch(['one', 'two'], {}, {'two': 'fail'}, SearchAccess('authenticated_admin'))
    finally:
        value.close()


def test_default_sequential_path_does_not_allocate_threads():
    value = resolver(1)
    value._derived_index_authorized = lambda *args, **kwargs: False
    assert value._authorize_source_batch(['one', 'two'], {}, {}, SearchAccess()) == {'one': False, 'two': False}
    assert value._authority_executor is None
    value.close()


def test_container_drains_related_requests_before_closing_authority_pool():
    from data_sync_etl.composition import Container
    closed = []
    container = Container.__new__(Container)
    for name in ['related_learning', 'graph_advisor', 'source_resolver', 'graph_driver']:
        setattr(container, name, SimpleNamespace(close=lambda name=name: closed.append(name)))
    container.close()
    assert closed == ['related_learning', 'graph_advisor', 'source_resolver', 'graph_driver']

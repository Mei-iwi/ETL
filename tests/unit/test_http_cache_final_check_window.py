"""Document the pre-existing final-check/response gap; not a cache acceptance PASS."""
import ast
import inspect
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.search_query_cache_support import configure
from tests.unit.test_search import searchable as searchable

ACCESS = SearchAccess('local_demo')


def test_existing_post_validation_writer_window_same_off_on(searchable, monkeypatch):
    c, version = searchable
    configure(c, version)
    lines, offset = inspect.getsourcelines(SearchService._content_search)
    tree = ast.parse(textwrap.dedent(''.join(lines))).body[0]
    # Search may also check its deadline before expensive work. This probe must
    # remain AFTER the final metadata/snapshot validation, not at the first check.
    line = max(node.lineno for node in tree.body if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == '_deadline') + offset - 1
    original = c.search._deadline
    outputs = []
    for enabled in (False, True):
        c.settings.search_query_embedding_cache_enabled = enabled
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'is_active': True})
        c.search.search_http('page', 'vector', access=ACCESS)
        checked, release = Event(), Event()
        def after_last_check(started, checked=checked, release=release):
            original(started)
            caller = sys._getframe(1)
            if caller.f_code is SearchService._content_search.__code__ and caller.f_lineno == line:
                checked.set()
                assert release.wait(10)
        with monkeypatch.context() as patch:
            patch.setattr(c.search, '_deadline', after_last_check)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(c.search.search_http, 'page', 'vector', access=ACCESS)
                try:
                    assert checked.wait(10)
                    with c.uow() as repo:
                        repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
                finally:
                    release.set()
                result = pending.result(timeout=10)
        # Counterexample to instantaneous global revocation, with or WITHOUT vector cache.
        assert result['items']
        outputs.append(result)
        assert c.search.search_http('page', 'vector', access=ACCESS)['items'] == []
        with pytest.raises(SearchError):
            c.search.context(result['items'][0]['context_id'], access=ACCESS)
    assert outputs[0] == outputs[1]

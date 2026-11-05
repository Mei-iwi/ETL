from copy import deepcopy
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.neo4j.resolver import candidate_ids
from data_sync_etl.application.graph_search import GraphSearchAdvisor
from data_sync_etl.config import Settings
from data_sync_etl.domain.graph_contract import GraphContractError


def mapping():
    return [{'content_unit_id':'root', 'resource_version_id':'version', 'resource_id':'resource',
             'subject_ids':['subject'], 'grade_ids':['grade']}]


def items():
    return [{'bag_id':'root', 'resource_version_id':'version', 'resource_id':'resource', 'score':0.5}]


def advisor(graph=None, authority=None, **overrides):
    settings = Settings(_env_file=None, neo4j_enabled=True, neo4j_search_enabled=True,
                        neo4j_password='test-only-private-secret', **overrides)
    return GraphSearchAdvisor(graph or SimpleNamespace(resolve=lambda ids: mapping()),
                               authority or SimpleNamespace(resolve=lambda ids: mapping()), settings)


def test_disabled_and_empty_never_touch_graph():
    def fail(ids):
        pytest.fail('Graph must not receive a request')
    a = advisor(SimpleNamespace(resolve=fail))
    a.settings.neo4j_search_enabled = False
    assert a.inspect(items()) == {}
    a.settings.neo4j_search_enabled = True
    assert a.inspect([]) == {}
    a.close()


def test_shadow_and_active_parity_preserves_input():
    a = advisor()
    before = deepcopy(items())
    assert a.inspect(before) == {}
    assert a.metrics['shadow'] == 1
    a.settings.neo4j_search_mode = 'active'
    assert a.inspect(before) == {'root':mapping()[0]}
    assert before == items()
    assert a.metrics['active_resolution'] == 1
    a.close()


def test_shadow_diagnostic_emits_only_internal_status_codes(caplog):
    import logging
    a = advisor()
    try:
        with caplog.at_level(logging.INFO, logger='etl'):
            assert a.inspect(items()) == {}
        assert '"stage": "graph_search"' in caplog.text
        assert '"action": "PARITY"' in caplog.text
        assert '"action": "SHADOW"' in caplog.text
        assert 'root' not in caplog.text
        assert 'test-only-private-secret' not in caplog.text
    finally:
        a.close()


@pytest.mark.parametrize('bad', [[], mapping()*2,
    [{**mapping()[0], 'resource_version_id':'wrong-version'}],
    [{**mapping()[0], 'resource_id':'private-other-resource'}],
    [{**mapping()[0], 'subject_ids':[]}], [{**mapping()[0], 'grade_ids':[]}],
    [{**mapping()[0], 'content_unit_id':'unrequested-private-unit'}],
])
def test_stale_missing_duplicate_or_unrequested_ids_fall_back(bad):
    a = advisor(SimpleNamespace(resolve=lambda ids: bad), neo4j_search_mode='active')
    assert a.inspect(items()) == {}
    assert a.metrics['unavailable_or_stale_fallback'] == 1
    a.close()


def test_current_postgres_mapping_change_falls_back():
    a = advisor(authority=SimpleNamespace(resolve=lambda ids: [{**mapping()[0], 'grade_ids':[]}]))
    assert a.inspect(items()) == {}
    a.close()


def test_unavailable_graph_fails_open_without_private_error_log(capsys):
    def fail(ids):
        raise RuntimeError('private connection detail must not escape')
    a = advisor(SimpleNamespace(resolve=fail), neo4j_search_mode='active')
    assert a.inspect(items()) == {}
    assert capsys.readouterr().out == ''
    a.close()


def test_slow_graph_budget_is_bounded_and_no_unbounded_queue():
    gate = Event()
    def slow(ids):
        gate.wait(3)
        return mapping()
    a = advisor(SimpleNamespace(resolve=slow))
    try:
        started = monotonic()
        for _ in range(4):
            assert a.inspect(items(), remaining=0.04) == {}
        assert monotonic()-started < 0.5
        assert a.metrics['timeout_fallback'] == 2
        assert a.metrics['busy_or_budget_fallback'] == 2
    finally:
        gate.set()
        a.close()


@pytest.mark.parametrize('ids', [[], ['a']*2, ['a']*101, [''], [' a']])
def test_graph_candidate_validation(ids):
    with pytest.raises(GraphContractError):
        candidate_ids(ids)

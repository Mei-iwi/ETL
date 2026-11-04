from copy import deepcopy
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data_sync_etl.application.related_learning import RelatedLearning
from data_sync_etl.domain.search_access import SearchAccess


def fixture(mode='active'):
    rows = {
        'seed': {'title': 'Seed', 'version_id': 'v1', 'source_hash': 'a' * 64,
                 'subject_ids': ['s'], 'grade_ids': ['g']},
        'other': {'title': 'Other', 'version_id': 'v2', 'source_hash': 'b' * 64,
                  'subject_ids': ['s'], 'grade_ids': ['g']},
    }
    authority = SimpleNamespace(read=Mock(side_effect=lambda *args: deepcopy(rows)))
    graph = SimpleNamespace(related=Mock(return_value=[{
        'resource_id': 'other', 'subject_ids': ['s'], 'grade_ids': ['g'],
    }]))
    settings = SimpleNamespace(neo4j_enabled=True, neo4j_search_enabled=True,
                               neo4j_search_mode=mode, neo4j_query_timeout_seconds=.1)
    return RelatedLearning(graph, authority, settings), rows


@pytest.mark.parametrize('mode', ['shadow', 'disabled'])
def test_nonactive_never_queries_graph_or_authority(mode):
    service, _ = fixture(mode)
    try:
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
        service.authority.read.assert_not_called()
        service.graph.related.assert_not_called()
    finally:
        service.close()


def test_active_uses_graph_but_returns_only_public_friendly_hydrated_fields():
    service, _ = fixture()
    try:
        result = service.run('seed', SearchAccess('authenticated_admin'))
        assert result == [{'resource_id': 'other', 'title': 'Other',
                           'reason_label': 'Cùng môn học và khối lớp', 'url': '/library/other'}]
        service.graph.related.assert_called_once_with('seed', ['other', 'seed'])
        assert service.authority.read.call_count == 2
    finally:
        service.close()


@pytest.mark.parametrize('bad', [
    {'resource_id': 'private', 'subject_ids': ['s'], 'grade_ids': ['g']},
    {'resource_id': 'seed', 'subject_ids': ['s'], 'grade_ids': ['g']},
    {'resource_id': 'other', 'subject_ids': ['unrelated'], 'grade_ids': ['g']},
    {'resource_id': 'other', 'subject_ids': [], 'grade_ids': []},
    {'resource_id': 'other', 'subject_ids': ['s'], 'grade_ids': ['g'], 'title': 'Untrusted'},
])
def test_denied_or_forged_graph_results_never_escape(bad):
    service, _ = fixture()
    service.graph.related.return_value = [bad]
    try:
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
    finally:
        service.close()


@pytest.mark.parametrize('field,value', [('version_id', 'new-version'), ('source_hash', 'changed'),
                                       ('title', 'Changed'), ('subject_ids', [])])
def test_permission_or_current_version_drift_hides_entire_enrichment(field, value):
    service, rows = fixture()
    fresh = deepcopy(rows)
    fresh['other'][field] = value
    service.authority.read.side_effect = [deepcopy(rows), fresh]
    try:
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
    finally:
        service.close()


def test_revoked_candidate_and_denied_seed_do_not_leak_counts_or_ids():
    service, rows = fixture()
    service.authority.read.side_effect = [rows, {}]
    try:
        assert service.run('seed', SearchAccess('authenticated_user')) == []
        service.authority.read.side_effect = lambda *args: {}
        service.graph.related.reset_mock()
        assert service.run('seed', SearchAccess('authenticated_user')) == []
        service.graph.related.assert_not_called()
    finally:
        service.close()


def test_graph_down_and_duplicate_candidates_fail_open():
    service, _ = fixture()
    try:
        service.graph.related.side_effect = RuntimeError('private connection details')
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
        service.graph.related.side_effect = None
        service.graph.related.return_value *= 2
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
    finally:
        service.close()


def test_related_cap_stable_order_and_reasons_do_not_change_search():
    service, rows = fixture()
    rows.update({key: {**deepcopy(rows['other']), 'title': key,
                      'grade_ids': ['g'] if key == 'both' else []}
                 for key in ('z', 'a', 'both', 'b')})
    service.graph.related.return_value = [
        {'resource_id': key, 'subject_ids': ['s'], 'grade_ids': rows[key]['grade_ids']}
        for key in ('z', 'a', 'both', 'b', 'other')]
    try:
        result = service.run('seed', SearchAccess('authenticated_admin'))
        assert [item['resource_id'] for item in result] == ['both', 'other', 'a']
        assert len(result) == 3
        assert result[-1]['reason_label'] == 'Cùng môn học'
    finally:
        service.close()


def test_public_and_empty_seed_never_start_graph():
    service, _ = fixture()
    try:
        assert service.run('seed', SearchAccess()) == []
        service.authority.read.assert_not_called()
        service.graph.related.assert_not_called()
        service.authority.read.side_effect = lambda *args: {}
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
        service.graph.related.assert_not_called()
    finally:
        service.close()


def test_timeout_and_saturation_are_bounded_and_never_block_search():
    service, _ = fixture()
    gate = Event()
    service.graph.related.side_effect = lambda *args: gate.wait(2)
    try:
        start = monotonic()
        access = SearchAccess('authenticated_admin')
        assert service.run('seed', access) == []
        assert service.run('seed', access) == []
        assert service.run('seed', access) == []
        assert monotonic() - start < .8
    finally:
        gate.set()
        service.close()

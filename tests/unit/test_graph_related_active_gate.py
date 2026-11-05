"""Independent graph-feature gate; never promotes the broader Search rollout."""

import pytest

from data_sync_etl.application.related_learning import RelatedSeedDenied
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_related_learning import fixture


def test_related_active_is_independent_of_search_shadow_or_disabled():
    service, _ = fixture('shadow')
    service.settings.neo4j_related_mode = 'active'
    service.settings.neo4j_search_enabled = False
    try:
        result = service.response('seed', SearchAccess('authenticated_admin'))
        assert result['status'] == 'ready' and result['items'][0]['resource_id'] == 'other'
        assert service.effective_mode == 'active'
        assert service.settings.neo4j_search_mode == 'shadow'
    finally:
        service.close()


@pytest.mark.parametrize('mode', ['shadow', 'disabled'])
def test_explicit_nonactive_related_hides_even_when_search_is_active(mode):
    service, _ = fixture('active')
    service.settings.neo4j_related_mode = mode
    try:
        assert service.response('seed', SearchAccess('authenticated_admin')) == {'items': [], 'status': 'disabled'}
        service.graph.related.assert_not_called()
        service.authority.read.assert_not_called()
    finally:
        service.close()


def test_outage_is_distinct_from_empty_and_sensitive_error_is_not_returned():
    service, _ = fixture()
    try:
        service.graph.related.return_value = []
        assert service.response('seed', SearchAccess('authenticated_admin')) == {'items': [], 'status': 'ready'}
        service.graph.related.side_effect = RuntimeError('private DSN must never escape')
        assert service.response('seed', SearchAccess('authenticated_admin')) == {'items': [], 'status': 'unavailable'}
    finally:
        service.close()


def test_strict_response_preserves_denied_seed_and_auth_failures():
    service, _ = fixture()
    class Denied(Exception):
        status = 403
    try:
        service.authority.read.return_value = {}
        service.authority.read.side_effect = None
        with pytest.raises(RelatedSeedDenied):
            service.response('seed', SearchAccess('authenticated_user'))
        service.graph.related.assert_not_called()
        service.authority.read.side_effect = Denied()
        with pytest.raises(Denied):
            service.response('seed', SearchAccess('authenticated_user'))
    finally:
        service.close()


def test_stale_graph_returns_unavailable_without_authorized_traversal():
    service, _ = fixture()
    service.graph.related.return_value = [{'resource_id': 'unmapped', 'subject_ids': [], 'grade_ids': []}]
    try:
        assert service.response('seed', SearchAccess('authenticated_admin')) == {'items': [], 'status': 'unavailable'}
    finally:
        service.close()

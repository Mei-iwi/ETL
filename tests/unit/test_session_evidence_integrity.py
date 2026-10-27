"""A light page must preserve the rank's exact version/bag/unit and score."""
import pytest

from data_sync_etl.application.search_session_gateway import SearchSessionGateway
from data_sync_etl.application.search_sessions import RankedHit
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess


@pytest.mark.parametrize('field,value', [('bag_id', 'other-bag'), ('content_unit_id', 'other-unit'),
                                        ('score', .001), ('resource_version_id', 'other-version')])
def test_changed_evidence_cannot_reuse_rank(field, value):
    hit = RankedHit('r', 'v', 'b', 'u', .9)
    item = dict(resource_id='r', resource_version_id='v', bag_id='b', content_unit_id='u', score=.9)
    item[field] = value
    gateway = SearchSessionGateway(lambda _: 'verified-fixture-r1', lambda *_: ([hit], False),
        lambda *_: [item], lambda *_: {'message': 'OK'})
    with pytest.raises(SearchError, match='SEARCH_SESSION_MISS'):
        gateway.read({'query': 'raw'}, SearchAccess('authenticated_admin', actor_id='alice'), page=1, size=5)

"""Synthetic hierarchy diagnostic; no DB/model/production latency claim."""
from types import SimpleNamespace

import pytest

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search_access import SearchAccess


def fixture(size=200, denied=None):
    resource = {'id': 'r', 'publication_status': 'PUBLISHED', 'is_active': True}
    version = {'id': 'v', 'resource_id': 'r', 'lifecycle_status': 'INGESTED'}
    units = [{'id': f'u{i}', 'resource_version_id': 'v', 'parent_unit_id': f'u{i-1}' if i else None,
              'review_status': 'REJECTED' if i == denied else 'APPROVED', 'retrieval_eligible': True}
             for i in range(size)]
    return SimpleNamespace(get=lambda *_: dict(resource)), (version, 1, units), SearchService.__new__(SearchService)


class CountingAccess:
    def __init__(self):
        self.calls = 0
        self.access = SearchAccess('local_demo')

    def allows(self, *args):
        self.calls += 1
        return self.access.allows(*args)


def reference_allowed(repo, snapshot, access):
    resource = repo.get('master_learning_resources', snapshot[0]['resource_id'])
    nodes = {unit['id']: unit for unit in snapshot[2]}
    allowed = set()
    for unit in snapshot[2]:
        if not access.allows(resource or {}, snapshot[0], unit):
            continue
        ancestor = unit
        while ancestor is not None and access.allows(resource, snapshot[0], ancestor):
            ancestor = nodes.get(ancestor['parent_unit_id'])
        if ancestor is None:
            allowed.add(unit['id'])
    return allowed


@pytest.mark.parametrize('denied', [None, 0, 70, 199])
def test_deep_hierarchy_has_same_rights_with_one_check_per_unit(denied):
    repo, snapshot, service = fixture(200, denied)
    expected = reference_allowed(repo, snapshot, SearchAccess('local_demo'))
    access = CountingAccess()
    assert service._allowed_units(repo, snapshot, access) == expected
    assert access.calls <= len(snapshot[2])


def test_cycle_fails_closed_and_checks_are_not_reused_across_snapshots():
    repo, snapshot, service = fixture(3)
    assert len(service._allowed_units(repo, snapshot, CountingAccess())) == 3
    snapshot[2][0]['parent_unit_id'] = 'u2'
    assert service._allowed_units(repo, snapshot, CountingAccess()) == set()
    snapshot[2][0]['parent_unit_id'] = None
    snapshot[2][0]['review_status'] = 'REJECTED'
    assert service._allowed_units(repo, snapshot, CountingAccess()) == set()

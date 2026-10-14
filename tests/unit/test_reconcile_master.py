from contextlib import contextmanager

import pytest
from conftest import ingest

from data_sync_etl.application.reconcile_master import ReconcileMaster
from data_sync_etl.domain.core import DomainError, ResourceNotFound
from data_sync_etl.domain.source_ownership import state_hash


class CompleteSource:
    source_origin = 'POSTGRESQL'

    def __init__(self):
        self.ids = ['resource-1', 'resource-2']
        self.calls = 0
        self.partial = False
        self.fail = False
        self.reappear = False

    @contextmanager
    def snapshot(self):
        self.calls += 1
        if self.reappear and self.calls == 3:
            self.ids.append('resource-3')
        yield self

    def reconciliation_summary(self, stream):
        return {'count': len(self.ids), 'minimum': min(self.ids) if self.ids else None, 'maximum': max(self.ids) if self.ids else None}

    def reconciliation_ids(self, stream, after, size):
        if self.fail and after:
            raise ConnectionError('PRIVATE_CONNECTION')
        if self.partial and after:
            return []
        return [key for key in sorted(self.ids) if after is None or key > after][:size]

    def reconciliation_present(self, stream, ids):
        return set(ids) & set(self.ids)


def owned_fixture(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        for row in repo.find('master_learning_resources'):
            # Explicit fixture approval, not production ownership inference.
            repo.update('master_learning_resources', row['id'], {'source_origin': 'POSTGRESQL', 'source_state_hash': state_hash(row)})
    source = CompleteSource()
    return source, ReconcileMaster(source, container.uow, batch_size=1, max_ratio=0.75)


@pytest.mark.parametrize('scenario', ['partial', 'fail', 'empty', 'reappear', 'local_change', 'max_affected'])
def test_unsafe_preview_or_apply_never_updates_target(container, scenario):
    source, service = owned_fixture(container)
    if scenario in ('partial', 'fail'):
        setattr(source, scenario, True)
    if scenario == 'empty':
        source.ids = []
    if scenario == 'local_change':
        with container.uow() as repo:
            repo.update('master_learning_resources', 'resource-3', {'title': 'Intentional local override'})
    if scenario == 'max_affected':
        service.max_affected = 1
        source.ids = ['resource-1']
    with container.uow() as repo:
        before = repo.find('master_learning_resources')
        checkpoints = repo.find('sync_checkpoints')
    preview = service.run('resources')
    if scenario == 'reappear':
        source.reappear = True
        result = service.run('resources', apply=True, fingerprint=preview['fingerprint'])
    else:
        result = preview
    assert result['status'] == 'BLOCKED', result
    with container.uow() as repo:
        assert repo.find('master_learning_resources') == before
        assert repo.find('sync_checkpoints') == checkpoints


def test_wrong_or_stale_plan_and_json_source_are_blocked(container):
    source, service = owned_fixture(container)
    preview = service.run('resources')
    assert service.run('resources', apply=True)['error_code'] == 'BLOCKED_PLAN_CHANGED'
    source.ids.append('resource-3')
    assert service.run('resources', apply=True, fingerprint=preview['fingerprint'])['error_code'] == 'BLOCKED_PLAN_CHANGED'
    assert ReconcileMaster(container.source, container.uow).run()['error_code'] == 'BLOCKED_SOURCE_ADAPTER'


def test_inactive_resource_has_no_detail_versions_content_or_new_ocr(container):
    version = ingest(container)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'is_active': False})
    with pytest.raises(ResourceNotFound):
        container.resource_detail.run('resource-1')
    with pytest.raises(ResourceNotFound):
        container.resource_versions.run('resource-1')
    with pytest.raises(DomainError):
        container.orchestrator.content_units(version['id'])
    with pytest.raises(DomainError):
        container.ocr.create_job(version['id'])
    with container.uow() as repo:
        assert repo.count('ocr_jobs', {}) == 0 and repo.get('resource_versions', version['id']) == version

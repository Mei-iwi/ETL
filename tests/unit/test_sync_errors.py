import pytest

from data_sync_etl.domain.core import DomainError
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


@pytest.mark.parametrize('code,status', [
    ('BLOCKED_SOURCE_IDENTITY', 409), ('BLOCKED_SOURCE_OWNERSHIP', 409),
    ('BLOCKED_LOCAL_SOURCE_CHANGE', 409), ('BLOCKED_MONGO_ID_MAPPING', 409),
    ('BLOCKED_SOURCE_MAPPING', 503),
])
@pytest.mark.parametrize('path', ['/api/v1/master-data/sync', '/admin/sync/master'])
def test_sync_returns_known_actionable_errors_without_adapter_details(container, monkeypatch, caplog, code, status, path):
    container.settings.admin_enabled = True

    def fail(*args, **kwargs):
        raise DomainError(code + ': PRIVATE_PASSWORD_AND_SOURCE_BODY')

    monkeypatch.setattr(container.sync, 'run', fail)
    with TestClient(create_app(container)) as client:
        response = client.post(path, json={'full': True})
    assert response.status_code == status
    assert response.json()['detail']['code'] == code
    assert response.json()['detail']['message']
    assert 'PRIVATE_PASSWORD_AND_SOURCE_BODY' not in response.text + caplog.text


def test_source_mapping_error_is_preserved_in_run_without_connection_details(container, monkeypatch):
    def unavailable(*args, **kwargs):
        raise DomainError('BLOCKED_SOURCE_MAPPING: PRIVATE_CONNECTION_DETAILS')

    monkeypatch.setattr(container.source, 'batches', unavailable)
    with pytest.raises(DomainError, match='^BLOCKED_SOURCE_MAPPING$'):
        container.sync.run('grades', full=True)
    with container.uow() as repo:
        run = repo.find('sync_runs', {'stream_name': 'grades'}, order=('-started_at',), limit=1)[0]
    assert run['status'] == 'FAILED' and run['error_message'] == 'BLOCKED_SOURCE_MAPPING'
    assert run['records_read'] == 0


def test_real_identity_guard_is_retained_and_checkpoint_is_not_advanced(container):
    from datetime import timedelta

    container.source.source_origin = 'POSTGRESQL'
    container.sync.run('grades', full=True)
    with container.uow() as repo:
        before = repo.get('sync_checkpoints', 'grades')
        grade = repo.get('master_grade_levels', 'grade-1')
    original = container.source.batches

    def reseeded(*args, **kwargs):
        for batch in original(*args, **kwargs):
            for aggregate in batch:
                aggregate.values['grade_created_at'] += timedelta(days=1)
            yield batch

    container.source.batches = reseeded
    with pytest.raises(DomainError, match='^BLOCKED_SOURCE_IDENTITY$'):
        container.sync.run('grades', full=True)
    with container.uow() as repo:
        assert repo.get('sync_checkpoints', 'grades') == before
        assert repo.get('master_grade_levels', 'grade-1') == grade

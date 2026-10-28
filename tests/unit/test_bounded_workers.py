from datetime import timedelta

import pytest
from pydantic import ValidationError

from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
from data_sync_etl.config import Settings
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.domain.core import now
from data_sync_etl.ports.processing import OCRResult
from tests.unit.test_source_file_import import importing as importing


def seed_imports(uow, resources=1, versions=1):
    with uow() as repo:
        return [repo.insert('source_file_imports', {
            'source_version_id': f'src-{r}-{v}', 'resource_id': f'r-{r}',
            'source_version_number': v, 'source_lifecycle_status': 'APPROVED',
            'source_updated_at': now(), 'storage_bucket': 'books',
            'storage_object_key': 'test.pdf', 'source_hash': 'a' * 64,
        }) for r in range(resources) for v in range(1, versions + 1)]


def test_import_resource_order_capacity_and_recovery(container):
    c = container
    c.settings.source_file_import_max_active = 2
    rows = seed_imports(c.uow, 2, 3)
    first = c.source_file_import.claim('a')
    second = c.source_file_import.claim('b')
    assert first['resource_id'] != second['resource_id']
    assert first['claim_token'] != second['claim_token']
    assert c.source_file_import.claim('c') is None
    with c.uow() as repo:
        repo.update('source_file_imports', first['id'], {
            'lease_expires_at': now() - timedelta(seconds=1)})
    recovered = c.source_file_import.claim('c')
    assert recovered['id'] == first['id']
    assert recovered['claim_token'] != first['claim_token']
    from data_sync_etl.application.source_file_import import SourceImportConflict
    with pytest.raises(SourceImportConflict):
        c.source_file_import.heartbeat(first)
    c.source_file_import.heartbeat(recovered)
    with c.uow() as repo:
        # A failed terminal mapping is visible to _parent; it is never retried forever.
        repo.update('source_file_imports', recovered['id'], {'status': 'FAILED'})
    third = c.source_file_import.claim('c')
    assert third['source_version_number'] == 2
    with c.uow() as repo:
        untouched = repo.get('source_file_imports', rows[2]['id'])
        assert untouched['attempts'] == 0


def test_import_retry_wait_does_not_spend_child_attempts(container):
    c = container
    rows = seed_imports(c.uow, versions=3)
    with c.uow() as repo:
        repo.update('source_file_imports', rows[0]['id'], {
            'status': 'RETRY_WAIT', 'next_retry_at': now() + timedelta(hours=1)})
    assert c.source_file_import.claim('a') is None
    with c.uow() as repo:
        assert all(row['attempts'] == 0 for row in repo.find('source_file_imports'))


def test_audit_stream_exclusion_and_capacity(container):
    c = container
    c.settings.reconciliation_max_active = 2
    q = AuditQueue(c.uow, c.settings)
    first = q.enqueue('METADATA', 'resources')
    waiting = q.enqueue('IDS', 'resources')
    other = q.enqueue('METADATA', 'subjects')
    third = q.enqueue('METADATA', 'users')
    assert q.claim(first)['id'] == first
    assert q.claim(waiting) is None
    assert q.claim(other)['id'] == other
    assert q.claim(third) is None
    with c.uow() as repo:
        assert repo.session.get(Job, waiting).attempts == 0
        assert repo.session.get(Job, third).attempts == 0
        repo.session.get(Job, first).lease_expires_at = now() - timedelta(seconds=1)
    recovered = q.claim(first)
    assert recovered['attempts'] == 2


@pytest.mark.parametrize('name', ['source_file_import_max_active', 'reconciliation_max_active'])
@pytest.mark.parametrize('value', [0, 3, -1])
def test_invalid_capacity(name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_ocr_expired_owner_cannot_renew_succeed_or_fail(container):
    from conftest import ingest
    c = container
    version = ingest(c)
    job = c.ocr.create_job(version['id'])
    old = c.ocr.claim('old')
    with c.uow() as repo:
        repo.update('ocr_page_tasks', old['id'], {
            'heartbeat_at': now() - timedelta(seconds=c.ocr.heartbeat_timeout + 1)})
    assert c.ocr.heartbeat(old) is False
    assert c.ocr.succeed(old, OCRResult('stale')) is False
    assert c.ocr.fail(old) is False
    assert c.ocr.recover_stale()['recovered'] == 1
    current = c.ocr.claim('new')
    assert current['id'] == old['id']
    assert current['claim_token'] != old['claim_token']
    assert c.ocr.succeed(old, OCRResult('stale')) is False
    assert c.ocr.succeed(current, OCRResult('valid')) is True
    with c.uow() as repo:
        assert repo.get('ocr_jobs', job['id'])['completed_pages'] == 1
        assert repo.count('ocr_page_results', {}) == 1


def test_expired_import_cannot_create_processing_job(importing, monkeypatch):
    c, _, _ = importing
    c.source_file_import.discover_once()
    claim = c.source_file_import.claim('old')
    original = c.ocr._create_job
    def expired(*args, **kwargs):
        with c.uow() as repo:
            repo.update('source_file_imports', claim['id'], {
                'lease_expires_at': now() - timedelta(seconds=1)})
        return original(*args, **kwargs)
    monkeypatch.setattr(c.ocr, '_create_job', expired)
    c.source_file_import.execute(claim)
    with c.uow() as repo:
        assert repo.count('ocr_jobs', {}) == 0
        assert repo.count('ocr_page_tasks', {}) == 0
        assert repo.get('source_file_imports', claim['id'])['target_version_id']
    recovered = c.source_file_import.claim('new')
    assert recovered['id'] == claim['id']
    assert recovered['claim_token'] != claim['claim_token']


@pytest.mark.parametrize('operation', ['create', 'retry'])
def test_processing_fence_rolls_back_transaction(container, operation):
    from conftest import ingest

    from data_sync_etl.domain.core import DomainError
    from tests.unit.test_processing_job_retry import failed_ocr
    c = container
    if operation == 'create':
        version = ingest(c)
        def run(fence):
            return c.ocr.create_job(version['id'], fence=fence)
    else:
        version, job = failed_ocr(c)
        def run(fence):
            return c.retry_processing_job.run(job['id'], fence=fence)
    calls = []
    def fence(repo):
        calls.append(True)
        if len(calls) == 2:
            raise DomainError('TEST_LEASE_LOST')
    with pytest.raises(DomainError, match='TEST_LEASE_LOST'):
        run(fence)
    with c.uow() as repo:
        if operation == 'create':
            assert repo.count('ocr_jobs', {}) == 0
            assert repo.count('ocr_page_tasks', {}) == 0
        else:
            assert repo.get('ocr_jobs', job['id'])['status'] == 'COMPLETED_WITH_ERRORS'
            assert repo.count('etl_stage_runs', {'stage': 'OCR_RETRY'}) == 0

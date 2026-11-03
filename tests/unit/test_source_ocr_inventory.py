from copy import deepcopy
from dataclasses import replace

import pytest

from data_sync_etl.application.source_ocr_inventory import AUDIT_KEY
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_source_ocr_runtime import runtime as runtime


def audit(r):
    if r.uow.get('sync_checkpoints', r.pipeline.inventory.stream):
        r.uow.update('sync_checkpoints', r.pipeline.inventory.stream, {'last_id': None})
    return r.pipeline.inventory.once(force=True)


def result(r):
    return r.uow.get('resource_versions', 'mv1')['processing_options'][AUDIT_KEY]


def test_draft_without_execution_contract_is_audited_waiting_not_silently_skipped(runtime, monkeypatch):
    r = runtime
    monkeypatch.setattr(r.pipeline, 'created_version_allowed', lambda *_: False)
    assert audit(r) == []
    assert result(r)['status'] == 'WAITING_FOR_PROCESS_AUTHORIZATION'
    assert result(r)['observed_status'] == 'ABSENT'
    assert result(r)['reason'] == 'SOURCE_OCR_VERSION_NOT_AUTHORIZED'
    assert r.pipeline.inventory.summary['inspected'] == 1
    assert not r.opens and not r.source.jobs and not r.content.pages


def test_authorized_absent_is_scheduled_by_existing_worker_and_only_once(runtime):
    r = runtime
    assert audit(r) == ['mv1']
    r.pipeline.inventory.next_scan = 0
    r.uow.update('sync_checkpoints', r.pipeline.inventory.stream, {'last_id': None})
    assert r.pipeline.schedule_authorized_source_job()
    assert len(r.source.jobs) == 1 and len(r.source.tasks) == 1
    assert not result(r)['needs_execution']
    before = len(r.opens)
    assert audit(r) == []  # Running work must not be re-enqueued or starve claim.
    assert len(r.opens) == before


def test_grant_rechecked_after_waiting_then_existing_scheduler_executes(runtime, monkeypatch):
    r = runtime
    access = r.pipeline._access
    def denied(*_):
        raise DomainError('SOURCE_OCR_ACTOR_GRANT_REQUIRED')
    monkeypatch.setattr(r.pipeline, '_access', denied)
    assert audit(r) == []
    assert result(r)['status'] == 'WAITING_FOR_PROCESS_AUTHORIZATION'
    monkeypatch.setattr(r.pipeline, '_access', access)
    assert audit(r) == ['mv1']
    assert result(r)['execution_authorized']


def test_complete_inventory_probe_has_no_file_or_recognizer_io(runtime, monkeypatch):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    r.container.worker.once('unit-worker')
    before = deepcopy((r.source.jobs, r.source.tasks, r.content.pages, r.content.units))
    monkeypatch.setattr(r.pipeline.resolver, 'open_verified_stream', lambda *_a, **_k: pytest.fail('opened'))
    monkeypatch.setattr(r.pipeline.recognizer, '_recognize_bytes', lambda *_a, **_k: pytest.fail('recognized'))
    assert audit(r) == []
    assert result(r)['status'] == 'COMPLETE_VERIFIED'
    assert (r.source.jobs, r.source.tasks, r.content.pages, r.content.units) == before


def test_mongo_outage_is_retryable_not_absent_and_cannot_enqueue(runtime, monkeypatch):
    r = runtime
    def unavailable(_):
        raise DomainError('SOURCE_MONGO_UNAVAILABLE')
    monkeypatch.setattr(r.content, 'has_ocr_pages', unavailable)
    assert audit(r) == []
    assert result(r)['status'] == 'RETRYABLE_ERROR'
    assert result(r)['attempts'] == 1
    assert not r.source.jobs and not r.opens


def test_source_lifecycle_outside_owner_contract_is_inventory_only(runtime):
    r = runtime
    r.source.versions['v1']['lifecycle_status'] = 'FAILED'
    assert audit(r) == []
    assert result(r)['status'] == 'WAITING_FOR_PROCESS_AUTHORIZATION'
    assert not result(r)['execution_authorized'] and not r.source.jobs and not r.opens
    assert result(r)['reason'] == 'SOURCE_OCR_LIFECYCLE_NOT_AUTHORIZED'


def test_exhausted_processing_is_visible_and_not_reenqueued(runtime):
    r = runtime
    r.uow.tables['source_file_imports']['i1']['attempts'] = r.container.settings.source_file_import_max_attempts
    assert audit(r) == []
    assert result(r)['status'] == 'RETRY_EXHAUSTED'
    assert not result(r)['needs_execution'] and not r.opens


def test_revoked_resource_is_classified_without_source_or_file_access(runtime, monkeypatch):
    r = runtime
    before = list(r.source.sql)
    r.uow.tables['master_learning_resources']['r1']['is_active'] = False
    assert audit(r) == []
    assert result(r)['status'] == 'INVALID_OR_REVOKED'
    assert r.source.sql == before and not r.opens


def test_external_job_without_import_or_pipeline_state_is_discovered_untrusted(runtime):
    r = runtime
    job = r.pipeline.create_job('mv1', access=r.access)
    source_before = deepcopy((r.source.jobs, r.source.tasks))
    r.uow.tables['source_file_imports'].clear()
    r.uow.tables['resource_versions']['mv1']['processing_options'] = {'source_version_identity': {
        'source_id': 'SRC_TEST', 'namespace': 'source-a', 'source_version_id': 'v1'}}
    assert audit(r) == []
    assert result(r)['external_status'] == 'UNTRUSTED'
    assert result(r)['reason'] == 'OWNER_PROVENANCE_CONTRACT_REQUIRED'
    assert (r.source.jobs, r.source.tasks) == source_before
    assert r.uow.get('ocr_jobs', job['id'])  # Existing unrelated projections not replaced.


def test_cursor_persists_and_wraps_for_late_or_old_metadata(runtime):
    r = runtime
    assert audit(r) == ['mv1']
    stream = r.pipeline.inventory.stream
    assert r.uow.get('sync_checkpoints', stream)['last_id'] == 'mv1'
    assert r.pipeline.inventory.once(force=True) == []
    assert r.uow.get('sync_checkpoints', stream)['last_id'] is None
    assert r.pipeline.inventory.once(force=True) == ['mv1']


def test_client_supplied_external_receipt_is_never_owner_provenance(runtime):
    r = runtime
    r.pipeline.create_job('mv1', access=r.access)
    options = {'source_version_identity': {'source_id': 'SRC_TEST', 'namespace': 'source-a', 'source_version_id': 'v1'},
               'external_receipt': {'trusted': True, 'signature': 'client-forged'}}
    r.uow.tables['resource_versions']['mv1']['processing_options'] = options
    assert audit(r) == []
    assert result(r)['external_status'] == 'UNTRUSTED'
    assert not r.content.pages and not r.content.units


def test_scope_excludes_other_resources_without_using_process_grant(runtime):
    r = runtime
    registration = r.pipeline.inventory._scope()[0]
    r.pipeline.inventory._scope = lambda: (registration, frozenset({'other-resource'}), {'id': 'viewer'})
    assert audit(r) == []
    assert r.pipeline.inventory.summary['discovered'] == 0
    assert '_source_ocr_audit' not in r.uow.get('resource_versions', 'mv1')['processing_options']


@pytest.mark.parametrize('grant', [True, False])
def test_approved_nonadmin_provider_keeps_process_and_view_separate(runtime, grant):
    # Explicit policy double: this is not an owner approval or a live positive.
    r = runtime
    from data_sync_etl.application.search import SearchService
    r.pipeline.contract = replace(r.pipeline.contract, roles=frozenset({'OPERATOR'}))
    r.pipeline.writer.contract = r.pipeline.contract
    access = SearchAccess('authenticated_user', frozenset({'r1'}) if grant else frozenset(),
                          'master:a1', 'a1', allow_owned_unreviewed=False)
    r.container.source_ocr_access_provider = lambda *_: access
    r.container.search.authorize_source_processing_version = SearchService.authorize_source_processing_version.__get__(
        r.container.search, SearchService)
    if not grant:
        with pytest.raises(SearchError):
            r.pipeline.create_job('mv1', access=access)
        assert not r.opens and not r.source.jobs
        return
    r.pipeline.create_job('mv1', access=access)
    assert r.container.worker.once('nonadmin-policy-double')
    resource = r.uow.get('master_learning_resources', 'r1')
    version = r.uow.get('resource_versions', 'mv1')
    unit = r.uow.find('content_units')[0]
    assert resource['publication_status'] == 'DRAFT'
    assert not access.allows(resource, version, unit)  # Process cannot mint view.


def test_inventory_api_does_not_grant_unauthenticated_global_inspection(runtime, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from data_sync_etl.api import routes
    app = FastAPI()
    app.state.container = runtime.container
    app.include_router(routes.router)
    monkeypatch.setattr(routes, 'services', lambda _: runtime.container)
    with TestClient(app) as client:
        assert client.get('/api/v1/system/source-ocr-inventory').status_code == 403


def test_metadata_status_exposes_sanitized_audit_without_private_checkpoint(runtime, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from data_sync_etl.api import routes
    r = runtime
    audit(r)
    app = FastAPI()
    app.state.container = r.container
    app.include_router(routes.router)
    monkeypatch.setattr(routes, 'services', lambda _: r.container)
    with TestClient(app) as client:
        response = client.get('/api/v1/resource-versions/mv1/etl-status')
    assert response.status_code == 200
    body = response.json()
    assert body['ocr_audit']['status'] == 'ABSENT'
    assert 'signature' not in body['ocr_audit']
    assert AUDIT_KEY not in body['version']['processing_options']

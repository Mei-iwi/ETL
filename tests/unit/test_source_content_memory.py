from datetime import timedelta

import pytest

from data_sync_etl.adapters.source.registry import ConfiguredSourceRegistry
from data_sync_etl.domain.core import DomainError, SourceFileError, now
from data_sync_etl.domain.source_registry import SourceCapabilities, SourceRegistration
from tests.unit.test_source_file_import import discover_and_import

pytest_plugins = ('tests.unit.test_source_file_import',)


@pytest.fixture
def source_importing(request):
    return request.getfixturevalue('importing')


def grant_source_content(container):
    locator = container.source.locator('resources', '__registry_probe__')
    registration = SourceRegistration(
        source_id='SRC_DEFAULT', backend=locator.backend, namespace=locator.namespace,
        adapter_type=type(container.source).__name__, capabilities=SourceCapabilities(
            read_metadata=True, read_content=True, process_content=True,
            index_derived_content=True, exact_resource_lookup=True,
            exact_version_lookup=True, file_reference_lookup=True))
    registry = ConfiguredSourceRegistry([(registration, container.source)])
    container.source_registry = registry
    container.source_resolver.source_registry = registry
    container.source_file_import.source_registry = registry


def test_content_capabilities_fail_closed_by_default():
    capabilities = SourceCapabilities()
    assert capabilities.read_metadata is True
    assert capabilities.read_content is False
    assert capabilities.process_content is False
    assert capabilities.index_derived_content is False


def test_process_command_allows_authorized_metadata_only_source(source_importing):
    container, _, _ = source_importing
    row = discover_and_import(container)
    grant_source_content(container)

    result = container.process_version.run(row['target_version_id'])

    assert result['job_status'] == 'PENDING'
    assert result['action'] == 'QUEUED'
    with container.uow() as repo:
        version = repo.get('resource_versions', row['target_version_id'])
        assert version['lifecycle_status'] == 'INGESTED'


def test_source_processing_options_are_local_and_idempotent_during_ocr(source_importing):
    container, _, _ = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    version_id = row['target_version_id']
    configured = container.bilingual.configure(version_id, {'source_language': 'en'})
    assert configured['status'] == 'configured'
    job = container.ocr.create_job(version_id)
    with container.uow.processing_lock(version_id) as acquired:
        assert acquired
        assert container.bilingual.configure(version_id, {'source_language': 'en'})['status'] == 'unchanged'
    with pytest.raises(DomainError, match='PROCESSING_BUSY'):
        container.bilingual.configure(version_id, {'source_language': 'fr'})
    with container.uow() as repo:
        assert len(repo.find('ocr_jobs', {'resource_version_id': version_id})) == 1
        assert repo.get('ocr_jobs', job['id'])['status'] == 'PENDING'


def test_source_processing_preferences_still_require_content_capabilities(source_importing):
    container, _, _ = source_importing
    row = discover_and_import(container)
    with pytest.raises(SourceFileError, match='SOURCE_CAPABILITY_BLOCKED'):
        container.bilingual.configure(row['target_version_id'], {'source_language': 'en'})


def test_authorized_source_reads_are_bounded_memory_and_never_saved(source_importing, monkeypatch):
    container, _, path = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    monkeypatch.setattr(container.storage, 'save_stream', lambda *a, **k: pytest.fail('SOURCE original persisted'))
    reference = container.source_resolver.resolve(row['target_version_id'])
    with container.source_resolver.open_verified_stream(reference, index=True) as stream:
        assert stream.read() == path.read_bytes()
        assert stream.seekable()
    assert not list(container.storage.root.rglob('*.pdf'))
    assert not list(container.settings.source_temp_root.glob('attempt-*'))


def test_authorized_source_native_pdf_extraction_uses_existing_ocr_jobs(source_importing):
    container, source_rows, path = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    job = container.ocr.create_job(row['target_version_id'])
    task = container.ocr.claim('source-memory-test')
    assert task and task['job_id'] == job['id']
    result, image_path = container.ocr.recognize(task)
    assert 'A real native text PDF for source import tests.' in result.text
    assert image_path is None
    assert container.ocr.succeed(task, result, image_path)
    from tests.indexing_support import setup_index
    setup_index(container)
    container.indexing.bags.search_candidates = lambda ids, model, limit: [
        bag for bag in container.indexing.bags.rows.values()
        if bag.resource_version_id in ids and bag.embedding_model == model and bag.is_current][:limit]
    container.finalize_bilingual(job['id'])
    container.indexing.enqueue(row['target_version_id'])
    container.indexing.once(row['target_version_id'])
    with container.uow() as repo:
        assert repo.count('ocr_page_results', {}) == 1
        version = repo.get('resource_versions', row['target_version_id'])
        imported = repo.get('source_file_imports', row['id'])
    assert version['lifecycle_status'] == 'INGESTED'
    assert imported['status'] == 'PROCESSING'
    assert container.indexing.status(row['target_version_id'])['index_status'] == 'READY'
    assert path.exists() and not list(container.storage.root.rglob('*.pdf'))
    assert not list(container.settings.source_temp_root.glob('attempt-*'))
    # Repeated discovery preserves completed content instead of suppressing all
    # search results. Also heal the state written by the earlier regression.
    from data_sync_etl.domain.search_access import SearchAccess
    access = SearchAccess('authenticated_user', allow_public=True, allow_shared_unreviewed=True)
    for damaged in (False, True):
        with container.uow() as repo:
            if damaged:
                repo.update('resource_versions', version['id'], {'lifecycle_status': 'METADATA_ONLY'})
                repo.update('source_file_imports', row['id'], {
                    'status': 'METADATA_ONLY', 'last_error_code': 'SOURCE_METADATA_ONLY'})
            container.source_file_import._discover(repo, source_rows[0])
        with container.uow() as repo:
            assert repo.get('resource_versions', version['id'])['lifecycle_status'] == 'INGESTED'
            assert repo.get('source_file_imports', row['id'])['status'] == 'PROCESSING'
        found = container.search.search('native', 'bow', access=access)
        assert found['total'] > 0
        context_id = found['items'][0]['context_url'].split('/')[-2]
        assert container.search.context(context_id, access=access)['chunks']


def test_viewer_flag_does_not_disable_authorized_source_ocr(source_importing):
    container, _, _ = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    container.settings.source_reference_enabled = False
    job = container.ocr.create_job(row['target_version_id'])
    assert job['status'] == 'PENDING'


def test_source_scheduler_does_not_starve_existing_jobs(source_importing, monkeypatch):
    container, _, _ = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    container.ocr.create_job(row['target_version_id'])
    with container.uow() as repo:
        repo.update('resource_versions', row['target_version_id'], {'lifecycle_status': 'METADATA_ONLY'})
        repo.update('source_file_imports', row['id'], {'status': 'METADATA_ONLY'})
    monkeypatch.setattr(container.ocr, 'create_job', lambda *a, **k: pytest.fail('Existing job rescheduled'))
    assert container.ocr.schedule_authorized_source_job() is False
    assert container.ocr.claim('worker-after-recovery') is not None


def test_source_prejob_failure_is_durable_bounded_and_can_be_retried(source_importing):
    container, source_rows, path = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    payload = path.read_bytes()
    path.unlink()
    assert container.ocr.schedule_authorized_source_job() is False
    with container.uow() as repo:
        failed = repo.get('source_file_imports', row['id'])
        assert failed['status'] == 'RETRY_WAIT'
        assert failed['attempts'] == 1 and failed['last_error_code'] == 'FILE_NOT_FOUND'
        assert failed['next_retry_at'] is not None
        container.source_file_import._discover(repo, source_rows[0])
    # Metadata discovery must not erase failure/backoff or pretend OCR ran.
    assert container.ocr.schedule_authorized_source_job() is False
    with container.uow() as repo:
        assert repo.get('source_file_imports', row['id'])['attempts'] == 1
        repo.update('source_file_imports', row['id'], {
            'attempts': container.settings.source_file_import_max_attempts - 1,
            'next_retry_at': now() - timedelta(seconds=1)})
    assert container.ocr.schedule_authorized_source_job() is False
    with container.uow() as repo:
        assert repo.get('source_file_imports', row['id'])['status'] == 'RETRY_EXHAUSTED'
    path.write_bytes(payload)
    assert container.source_file_import.retry(row['id'])['action'] == 'QUEUED'
    assert container.ocr.schedule_authorized_source_job() is True
    assert container.ocr.schedule_authorized_source_job() is False
    with container.uow() as repo:
        assert repo.count('ocr_jobs', {'resource_version_id': row['target_version_id']}) == 1


def test_source_batch_authorization_never_reuses_revoked_authority(source_importing):
    from data_sync_etl.domain.search_access import SearchAccess
    container, _, _ = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    access = SearchAccess('authenticated_admin')
    assert container.source_resolver.derived_index_authorized_many([row['target_version_id']], access=access) == {
        row['target_version_id']: True}
    with container.uow() as repo:
        repo.update('source_file_imports', row['id'], {'source_resource_active': False})
    assert container.source_resolver.derived_index_authorized_many([row['target_version_id']], access=access) == {
        row['target_version_id']: False}


def test_source_manual_ocr_retry_reuses_file_version_and_job(source_importing, monkeypatch):
    container, _, path = source_importing
    row = discover_and_import(container)
    grant_source_content(container)
    job = container.ocr.create_job(row['target_version_id'])
    container.ocr.max_retries = 1
    task = container.ocr.claim('fail-once')
    container.ocr.fail(task, error=DomainError('EMPTY_EXTRACTED_CONTENT'))
    monkeypatch.setattr(container.ocr, 'create_job', lambda *a, **k: pytest.fail('Duplicate job'))
    assert container.source_file_import.retry(row['id'])['action'] == 'QUEUED'
    with container.uow() as repo:
        assert repo.get('ocr_jobs', job['id'])['status'] == 'PENDING'
        assert repo.get('source_file_imports', row['id'])['processing_job_id'] == job['id']
        assert repo.count('ocr_jobs', {'resource_version_id': row['target_version_id']}) == 1
    assert path.is_file() and not list(container.storage.root.rglob('*.pdf'))


def test_source_cannot_process_when_derived_index_grant_is_missing(source_importing):
    container, _, _ = source_importing
    row = discover_and_import(container)
    locator = container.source.locator('resources', '__registry_probe__')
    registration = SourceRegistration(
        source_id='SRC_DEFAULT', backend=locator.backend, namespace=locator.namespace,
        adapter_type=type(container.source).__name__, capabilities=SourceCapabilities(
            read_content=True, process_content=True, exact_version_lookup=True))
    container.source_resolver.source_registry = ConfiguredSourceRegistry([(registration, container.source)])
    with pytest.raises(SourceFileError, match='SOURCE_CAPABILITY_BLOCKED'):
        container.ocr.create_job(row['target_version_id'])
    with container.uow() as repo:
        assert repo.count('ocr_jobs', {}) == 0
        assert repo.get('resource_versions', row['target_version_id'])['lifecycle_status'] == 'METADATA_ONLY'

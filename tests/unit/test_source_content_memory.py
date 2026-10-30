import pytest

from data_sync_etl.adapters.source.registry import ConfiguredSourceRegistry
from data_sync_etl.domain.core import SourceFileError
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

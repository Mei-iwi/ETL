import pytest

from data_sync_etl.application.source_file_import import (
    METADATA_ONLY_BUCKET,
    METADATA_ONLY_ERROR,
)

pytest_plugins = ("tests.unit.test_source_file_import",)


def test_source_discovery_is_metadata_only_by_default(importing, monkeypatch):
    container, _, _ = importing
    container.settings.source_reference_enabled = False
    container.settings.source_temporary_files_enabled = False

    def forbidden(*args, **kwargs):
        raise AssertionError('SOURCE bytes must not be read during metadata discovery')

    monkeypatch.setattr(container.source_files, 'open_stream', forbidden)
    monkeypatch.setattr(container.source_resolver, 'temporary_path', forbidden)
    assert container.source_file_import.discover_once() == 1

    with container.uow() as repo:
        row, = repo.find('source_file_imports')
        version = repo.get('resource_versions', row['target_version_id'])
        assert row['status'] == 'METADATA_ONLY'
        assert row['last_error_code'] == METADATA_ONLY_ERROR
        assert row['storage_bucket'] == METADATA_ONLY_BUCKET
        assert row['storage_object_key'].startswith(f'{METADATA_ONLY_BUCKET}/')
        assert version['lifecycle_status'] == 'METADATA_ONLY'
        assert version['storage_bucket'] == METADATA_ONLY_BUCKET
        assert version['storage_object_key'] == row['storage_object_key']
    assert container.source_file_import.claim() is None


def test_metadata_only_never_transitions_to_external_source_content(importing, monkeypatch):
    c, _, _ = importing
    c.settings.source_reference_enabled = c.settings.source_temporary_files_enabled = False
    assert c.source_file_import.discover_once() == 1
    with c.uow() as repo:
        original, = repo.find('source_file_imports')
    c.settings.source_reference_enabled = c.settings.source_temporary_files_enabled = True
    c.source_file_import._media_policy_scan_started = False
    monkeypatch.setattr(c.source_files, 'open_stream', lambda *_a, **_k: pytest.fail('No SOURCE read'))
    assert c.source_file_import.discover_once() == 0
    with c.uow() as repo:
        row = repo.get('source_file_imports', original['id'])
        assert row['status'] == 'METADATA_ONLY' and row['target_version_id'] == original['target_version_id']
        assert row['storage_bucket'] == METADATA_ONLY_BUCKET
    assert c.source_file_import.claim() is None


def test_metadata_only_mutation_cannot_resume(importing):
    c, source, _ = importing
    c.settings.source_reference_enabled = c.settings.source_temporary_files_enabled = False
    c.source_file_import.discover_once()
    source[0]['source_hash'] = 'b' * 64
    with c.uow() as repo:
        c.source_file_import._discover(repo, source[0])
        row, = repo.find('source_file_imports')
        assert row['status'] == 'FAILED' and row['last_error_code'] == 'SOURCE_VERSION_MUTATED'

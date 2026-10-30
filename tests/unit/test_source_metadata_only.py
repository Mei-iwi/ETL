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

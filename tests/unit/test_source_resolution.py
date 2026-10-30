from data_sync_etl.domain.core import now
from data_sync_etl.domain.search_access import SearchAccess


def seed_source_mapping(container):
    container.sync.run(full=True)
    source = {
        'source_version_id': 'source-resolution-v1', 'resource_id': 'resource-1',
        'source_version_number': 1, 'parent_version_id': None, 'source_hash': 'a' * 64,
        'source_lifecycle_status': 'APPROVED', 'resource_active': True,
        'resource_deleted_at': None, 'updated_at': now(), 'resource_title': 'Resolved source',
        'storage_bucket': 'hidden', 'storage_object_key': 'private/file.pdf',
    }
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {
            'source_origin': 'POSTGRESQL', 'publication_status': 'PUBLISHED',
            'source_identity_hash': 'b' * 64,
        })
        version = repo.insert('resource_versions', {
            'resource_id': 'resource-1', 'version_number': 1, 'lifecycle_status': 'METADATA_ONLY',
            'storage_bucket': '__SOURCE_METADATA_ONLY__',
            'storage_object_key': '__SOURCE_METADATA_ONLY__/source-resolution-v1.pdf',
            'source_hash': source['source_hash'],
        })
        repo.insert('source_file_imports', {
            'source_system': 'postgresql', 'source_version_id': source['source_version_id'],
            'resource_id': 'resource-1', 'resource_title': source['resource_title'],
            'source_version_number': 1, 'source_lifecycle_status': 'APPROVED',
            'source_resource_active': True, 'source_updated_at': source['updated_at'],
            'storage_bucket': '__SOURCE_METADATA_ONLY__',
            'storage_object_key': '__SOURCE_METADATA_ONLY__/source-resolution-v1.pdf',
            'source_hash': source['source_hash'], 'status': 'METADATA_ONLY',
            'last_error_code': 'SOURCE_METADATA_ONLY', 'target_version_id': version['id'],
        })
    return source


def test_source_resolution_uses_exact_id_without_file_access(container, monkeypatch):
    source = seed_source_mapping(container)
    calls = []

    def get_version(source_version_id):
        calls.append(source_version_id)
        return source if source_version_id == source['source_version_id'] else None

    monkeypatch.setattr(container.source, 'get_version', get_version, raising=False)
    result = container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))
    assert result['resolution_status'] == 'available'
    assert result['content_availability'] == 'metadata-only'
    assert result['source_record_available'] is True
    assert result['source_identity'] == 'b' * 64
    assert calls == [source['source_version_id']]
    assert 'storage_bucket' not in result and 'storage_object_key' not in result


def test_source_resolution_fails_closed_for_stale_and_unauthorized(container, monkeypatch):
    source = seed_source_mapping(container)
    monkeypatch.setattr(container.source, 'get_version', lambda _: {**source, 'source_hash': 'c' * 64}, raising=False)
    stale = container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))
    assert stale['resolution_status'] == 'stale' and not stale['source_record_available']
    try:
        container.source_resolution.resolve('resource-1', SearchAccess())
    except Exception as exc:
        assert getattr(exc, 'code', None) == 'SEARCH_ACCESS_RESTRICTED'
    else:
        raise AssertionError('public access must not resolve SOURCE metadata')


def test_source_resolution_hides_missing_source_and_unowned_metadata(container, monkeypatch):
    seed_source_mapping(container)
    monkeypatch.setattr(container.source, 'get_version', lambda _: None, raising=False)
    unavailable = container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))
    assert unavailable['resolution_status'] == 'unavailable'
    assert unavailable['source_record_available'] is False

    unowned = SearchAccess('authenticated_user', frozenset({'other-resource'}), 'master:other', 'other')
    try:
        container.source_resolution.resolve('resource-1', unowned)
    except Exception as exc:
        assert getattr(exc, 'code', None) == 'RESOURCE_NOT_FOUND'
    else:
        raise AssertionError('unowned metadata must remain hidden')

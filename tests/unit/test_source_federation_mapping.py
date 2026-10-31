import json
import shutil
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect

from data_sync_etl.adapters.persistence.source_identity_mapping import (
    SQLAlchemySourceIdentityMappingStore,
)
from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.adapters.source.registry import ConfiguredSourceRegistry
from data_sync_etl.application.reconcile_master import ReconcileMaster
from data_sync_etl.application.source_open import SourceOpenResolver
from data_sync_etl.application.source_resolution import SourceResolutionService
from data_sync_etl.application.source_router import SourceRouter
from data_sync_etl.application.source_version_sync import SourceVersionSynchronizer
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.composition import create_source_registry
from data_sync_etl.db.models import Base, SyncCheckpoint
from data_sync_etl.domain.core import DomainError, SourceFileError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
from data_sync_etl.domain.source_ownership import state_hash
from data_sync_etl.domain.source_registry import SourceCapabilities, SourceRegistration
from data_sync_etl.workers.master_sync import MasterSyncWorker


class _Source:
    def __init__(self, namespace):
        self.namespace = namespace

    def locator(self, entity_type, source_id):
        return SourceLocator(backend='postgresql', namespace=self.namespace,
                             entity_type=entity_type, source_record_id=source_id)


def _registration(source_id, namespace):
    return SourceRegistration(source_id=source_id, backend='postgresql', namespace=namespace,
                              adapter_type='test', enabled=True,
                              capabilities=SourceCapabilities(read_metadata=True))


def test_federation_uses_existing_schema_and_defines_no_persistence_tables():
    expected_business_tables = {
        'content_unit_texts', 'content_units', 'etl_stage_runs', 'master_grade_levels',
        'master_learning_resources', 'master_users', 'ocr_jobs', 'ocr_page_results',
        'ocr_page_tasks', 'reconciliation_audit_jobs', 'reconciliation_file_states',
        'resource_deletion_events', 'resource_grade_levels', 'resource_subjects',
        'resource_versions', 'source_file_imports', 'subjects', 'sync_auto_config',
        'sync_auto_streams', 'sync_checkpoints', 'sync_reconciliation_discrepancies',
        'sync_reconciliation_runs', 'sync_runs',
    }
    assert set(Base.metadata.tables) == expected_business_tables
    forbidden = {'source_registry', 'source_identity_mappings', 'source_sync_checkpoints'}
    assert forbidden.isdisjoint(Base.metadata.tables)
    assert 'sync_checkpoints' in Base.metadata.tables
    assert 'master_learning_resources' in Base.metadata.tables
    with pytest.raises(ValueError):
        SourceRegistration(source_id='SRC_A', backend='postgresql', namespace='school-a',
                           adapter_type='postgresql', connection_ref='postgresql://private')
    from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository

    with pytest.raises(DomainError, match='BLOCKED_SOURCE_CHECKPOINT_KEY'):
        SQLAlchemyRepository._source_checkpoint_key('S' * 21, 'resources')


def test_external_viewer_registration_requires_explicit_host_allowlist():
    with pytest.raises(ValueError):
        SourceRegistration(source_id='SRC_WILDCARD', backend='postgresql', namespace='school-x',
                           adapter_type='test', capabilities=SourceCapabilities(external_viewer=True),
                           viewer_hosts=('*.school-x.edu',))
    with pytest.raises(DomainError, match='SOURCE_VIEWER_ALLOWLIST_REQUIRED'):
        ConfiguredSourceRegistry([(
            SourceRegistration(source_id='SRC_A', backend='postgresql', namespace='school-a',
                               adapter_type='test', capabilities=SourceCapabilities(external_viewer=True)),
            _Source('school-a'),
        )])
    registration = SourceRegistration(
        source_id='SRC_A', backend='postgresql', namespace='school-a', adapter_type='test',
        capabilities=SourceCapabilities(external_viewer=True), viewer_hosts=('viewer.school-a.edu',))
    ConfiguredSourceRegistry([(registration, _Source('school-a'))])
    from data_sync_etl.application.source_open import SourceOpenResolver

    assert SourceOpenResolver._allowed_viewer(
        'https://viewer.school-a.edu/open/1', {'viewer.school-a.edu'})
    assert not SourceOpenResolver._allowed_viewer(
        'https://viewer.school-a.edu/open/1?signature=secret', {'viewer.school-a.edu'})
    assert not SourceOpenResolver._allowed_viewer(
        'https://other.example/open/1', {'viewer.school-a.edu'})
    assert not SourceOpenResolver._allowed_viewer(
        'http://viewer.school-a.edu/open/1', {'viewer.school-a.edu'})
    assert not SourceOpenResolver._allowed_viewer(
        'https://drive.google.com/file/d/1/view?resourcekey=private', {'drive.google.com'})


def test_default_drive_viewer_capability_requires_explicit_drive_configuration(container):
    disabled = create_source_registry(container.settings, container.source).registrations()[0]
    assert disabled.capabilities.file_reference_lookup is False
    assert disabled.capabilities.external_viewer is False
    settings = container.settings.model_copy(update={
        'source_drive_enabled': True, 'source_drive_buckets': {'DRIVE': 'root'}})
    enabled = create_source_registry(settings, container.source).registrations()[0]
    assert enabled.capabilities.file_reference_lookup is True
    assert enabled.capabilities.external_viewer is True
    assert set(enabled.viewer_hosts) == {'drive.google.com', 'docs.google.com'}


def test_federated_source_file_worker_cannot_discover_or_claim_downloads(container, monkeypatch):
    service = container.source_file_import
    service.settings = service.settings.model_copy(update={'source_federation_enabled': True})
    calls = []
    monkeypatch.setattr(service.files, 'open_stream', lambda *args, **kwargs: calls.append('open_stream'), raising=False)
    monkeypatch.setattr(service.resolver, 'temporary_path', lambda *args, **kwargs: calls.append('temporary_path'), raising=False)
    assert service._metadata_only_policy() is True
    assert service._media_processing_enabled() is False
    assert service.availability()['file_sync_error'] == 'SOURCE_ORIGINAL_IMPORT_DISABLED'
    assert service.discover_once() == 0
    assert service.claim('federated-worker') is None
    with pytest.raises(SourceFileError, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        service._import({'id': 'must-not-be-read'})
    assert calls == []


def test_source_version_sync_namespaces_colliding_ids_and_reuses_existing_tables(container):
    container.sync.run(full=True)
    locator_a = SourceLocator(backend='postgresql', namespace='school-a',
                              entity_type='resources', source_record_id='resource-1')
    locator_b = SourceLocator(backend='postgresql', namespace='school-b',
                              entity_type='resources', source_record_id='resource-1')
    with container.uow() as repo:
        original = repo.get('master_learning_resources', 'resource-1')
        master_a = {**original, 'source_origin': 'POSTGRESQL',
                    'source_identity_version': locator_a.version, 'source_backend': locator_a.backend,
                    'source_namespace': locator_a.namespace, 'source_entity_type': locator_a.entity_type,
                    'source_record_key': locator_a.source_record_id,
                    'source_identity_hash': source_identity_hash(locator_a)}
        master_a['source_state_hash'] = state_hash(master_a)
        repo.update('master_learning_resources', original['id'], {
            key: value for key, value in master_a.items() if key != 'id'})
        master_b = {**original, 'id': 'MASTER_RESOURCE_B', 'mongo_id': None,
                    'title': 'Independent school B resource', 'source_origin': 'POSTGRESQL',
                    'source_identity_version': locator_b.version, 'source_backend': locator_b.backend,
                    'source_namespace': locator_b.namespace, 'source_entity_type': locator_b.entity_type,
                    'source_record_key': locator_b.source_record_id,
                    'source_identity_hash': source_identity_hash(locator_b)}
        master_b['source_state_hash'] = state_hash(master_b)
        repo.insert('master_learning_resources', master_b)

    class _VersionSource(_Source):
        source_backend = 'postgresql'

        def __init__(self, namespace):
            super().__init__(namespace)
            self.source_namespace = namespace
            self.rows = [
                {'source_version_id': 'VER001', 'resource_id': 'resource-1', 'source_version_number': 1,
                 'parent_version_id': None, 'source_hash': 'a' * 64, 'source_lifecycle_status': 'PUBLISHED',
                 'updated_at': datetime.now(UTC), 'resource_active': True},
                {'source_version_id': 'VER002', 'resource_id': 'resource-1', 'source_version_number': 2,
                 'parent_version_id': 'VER001', 'source_hash': 'b' * 64, 'source_lifecycle_status': 'PUBLISHED',
                 'updated_at': datetime.now(UTC), 'resource_active': True},
            ]

        def version_page(self, after=None, size=1):
            rows = [row for row in self.rows if after is None or row['source_version_id'] > after]
            return {'items': rows[:size], 'next_cursor': rows[size - 1]['source_version_id']
                    if len(rows) > size else None}

        def get_version(self, source_version_id):
            row = next((row for row in self.rows if row['source_version_id'] == source_version_id), None)
            if row is None:
                return None
            return {**row, 'resource_publication_status': 'PUBLISHED'}

        def list_file_references(self, source_version_id):
            row = self.get_version(source_version_id)
            if row is None:
                return []
            return [{
                'source_resource_id': row['resource_id'],
                'source_version_id': source_version_id,
                'source_file_id': 'FILE001',
                'file_name': f"{self.namespace}-{source_version_id}.pdf",
                'mime_type': 'application/pdf', 'revision': row['source_hash'],
                'access_status': 'ALLOWED',
                'viewer_url': f"https://viewer.{self.namespace}.edu/open/{source_version_id}",
            }]

    source_a, source_b = _VersionSource('school-a'), _VersionSource('school-b')
    synchronizer = SourceVersionSynchronizer(container.uow, batch_size=1)
    before_tables = set(inspect(container.engine).get_table_names())
    for source_id, source in (('SRC_A', source_a), ('SRC_B', source_b)):
        result = synchronizer.sync(source_id, source)
        assert result == {'status': 'COMPLETED', 'inserted': 2, 'updated': 0, 'unchanged': 0}

    with container.uow() as repo:
        imports_a = {row['source_version_id']: row for row in repo.find('source_file_imports', {'source_system': 'SRC_A'})}
        imports_b = {row['source_version_id']: row for row in repo.find('source_file_imports', {'source_system': 'SRC_B'})}
        versions_a = {key: repo.get('resource_versions', row['target_version_id']) for key, row in imports_a.items()}
        versions_b = {key: repo.get('resource_versions', row['target_version_id']) for key, row in imports_b.items()}
        assert set(imports_a) == set(imports_b) == {'VER001', 'VER002'}
        assert versions_a['VER001']['id'] != versions_b['VER001']['id']
        assert versions_a['VER001']['id'] != 'VER001'
        assert versions_a['VER002']['parent_version_id'] == versions_a['VER001']['id']
        assert versions_b['VER002']['parent_version_id'] == versions_b['VER001']['id']
        assert versions_a['VER002']['parent_version_id'] != versions_b['VER001']['id']
        assert all(row['status'] == 'METADATA_ONLY' for row in (*imports_a.values(), *imports_b.values()))
    registrations = [
        (SourceRegistration(source_id='SRC_A', backend='postgresql', namespace='school-a',
                           adapter_type='test', capabilities=SourceCapabilities(
                               exact_resource_lookup=True, exact_version_lookup=True,
                               file_reference_lookup=True, external_viewer=True),
                           viewer_hosts=('viewer.school-a.edu',)), source_a),
        (SourceRegistration(source_id='SRC_B', backend='postgresql', namespace='school-b',
                           adapter_type='test', capabilities=SourceCapabilities(
                               exact_resource_lookup=True, exact_version_lookup=True,
                               file_reference_lookup=True, external_viewer=True),
                           viewer_hosts=('viewer.school-b.edu',)), source_b),
    ]
    registry = ConfiguredSourceRegistry(registrations)
    router = SourceRouter(registry, SQLAlchemySourceIdentityMappingStore(container.uow, registry))
    container.settings.source_reference_enabled = True
    resolution = SourceResolutionService(container.uow, source_a, container.settings, router=router)
    opener = SourceOpenResolver(container.uow, registry, router, resolution)
    open_a = opener.resolve('resource-1', SearchAccess(scope='authenticated_admin'), versions_a['VER001']['id'])
    open_b = opener.resolve('MASTER_RESOURCE_B', SearchAccess(scope='authenticated_admin'), versions_b['VER001']['id'])
    assert open_a['status'] == open_b['status'] == 'EXTERNAL_VIEWER_AVAILABLE'
    assert open_a['files'][0]['source_file_id'] == open_b['files'][0]['source_file_id'] == 'FILE001'
    assert 'source_resource_id' not in open_a['files'][0] and 'source_version_id' not in open_a['files'][0]
    assert open_a['files'][0]['viewer_url'] == 'https://viewer.school-a.edu/open/VER001'
    assert open_b['files'][0]['viewer_url'] == 'https://viewer.school-b.edu/open/VER001'
    source_a.rows[0].update(storage_bucket='DRIVE', storage_object_key='files/blob/book.pdf')
    source_a.list_file_references = None
    source_a_registration = SourceRegistration(
        source_id='SRC_A', backend='postgresql', namespace='school-a', adapter_type='test',
        capabilities=SourceCapabilities(exact_resource_lookup=True, exact_version_lookup=True,
                                        file_reference_lookup=True, external_viewer=True),
        viewer_hosts=('drive.google.com',))
    drive_registry = ConfiguredSourceRegistry([(source_a_registration, source_a)])
    drive_router = SourceRouter(drive_registry, SQLAlchemySourceIdentityMappingStore(container.uow, drive_registry))
    drive_resolution = SourceResolutionService(container.uow, source_a, container.settings, router=drive_router)

    class DriveReferenceReader:
        def viewer_reference(self, bucket, object_key):
            assert (bucket, object_key) == ('DRIVE', 'files/blob/book.pdf')
            # A concurrent metadata poll must not invalidate the same file.
            with container.uow() as repo:
                imported = repo.find('source_file_imports', {'target_version_id': versions_a['VER001']['id']})[0]
                repo.update('source_file_imports', imported['id'], {'updated_at': datetime.now(UTC)})
            return {'source_file_id': 'blob', 'file_name': 'book.pdf', 'mime_type': 'application/pdf',
                    'revision': 'drive-revision-7',
                    'viewer_url': 'https://drive.google.com/file/d/blob/view',
                    'embedded_url': 'https://drive.google.com/file/d/blob/preview'}

    drive_opener = SourceOpenResolver(container.uow, drive_registry, drive_router,
                                      drive_resolution, file_sources=DriveReferenceReader())
    drive_resolution.content_authorizer = lambda *a, **k: pytest.fail('Viewer must not require INDEX_READY')
    drive_open = drive_opener.resolve('resource-1', SearchAccess(scope='authenticated_admin'),
                                      versions_a['VER001']['id'])
    assert drive_open['status'] == 'EXTERNAL_VIEWER_AVAILABLE'
    assert drive_open['files'][0]['viewer_url'] == 'https://drive.google.com/file/d/blob/view'
    assert drive_open['files'][0]['embedded_url'] == 'https://drive.google.com/file/d/blob/preview'
    b_files = source_b.list_file_references
    source_b.list_file_references = lambda version_id: [
        {**b_files(version_id)[0], 'access_status': 'AUTH_REQUIRED'}]
    auth_required = opener.resolve(
        'MASTER_RESOURCE_B', SearchAccess(scope='authenticated_admin'), versions_b['VER002']['id'])
    assert auth_required['status'] == 'SOURCE_AUTH_REQUIRED'
    assert auth_required['files'][0]['viewer_url'] is None
    source_b.list_file_references = b_files
    original_source_a_rows = list(source_a.rows)
    source_a.rows = source_a.rows[:1]
    assert synchronizer.sync('SRC_A', source_a)['inserted'] == 0
    with container.uow() as repo:
        missing_a = repo.find('source_file_imports', {
            'source_system': 'SRC_A', 'source_version_id': 'VER002'}, limit=1)[0]
        still_present_b = repo.find('source_file_imports', {
            'source_system': 'SRC_B', 'source_version_id': 'VER002'}, limit=1)[0]
        assert missing_a['status'] == 'FAILED'
        assert missing_a['last_error_code'] == 'SOURCE_VERSION_MISSING'
        assert repo.get('resource_versions', missing_a['target_version_id'])['lifecycle_status'] == 'REVOKED'
        assert still_present_b['status'] == 'METADATA_ONLY'
    source_a.rows = original_source_a_rows
    assert synchronizer.sync('SRC_A', source_a)['updated'] == 1
    with container.uow() as repo:
        restored_a = repo.find('source_file_imports', {
            'source_system': 'SRC_A', 'source_version_id': 'VER002'}, limit=1)[0]
        assert restored_a['status'] == 'METADATA_ONLY'
        assert repo.get('resource_versions', restored_a['target_version_id'])['lifecycle_status'] == 'METADATA_ONLY'
    assert set(inspect(container.engine).get_table_names()) == before_tables


def test_registry_routes_same_source_key_to_exact_namespaced_adapter_and_fails_closed():
    a, b = _Source('school-a'), _Source('school-b')
    registry = ConfiguredSourceRegistry([(_registration('SRC_A', 'school-a'), a),
                                         (_registration('SRC_B', 'school-b'), b)])
    route_a = registry.adapter_for(a.locator('resources', 'RES001'))
    route_b = registry.adapter_for(b.locator('resources', 'RES001'))
    assert route_a is a
    assert route_b is b

    with pytest.raises(DomainError, match='SOURCE_REGISTRY_NAMESPACE_UNKNOWN'):
        registry.adapter_for(SourceLocator(backend='postgresql', namespace='school-c',
                                           entity_type='resources', source_record_id='RES001'))
    with pytest.raises(DomainError, match='SOURCE_CAPABILITY_BLOCKED'):
        registry.adapter_for(a.locator('resources', 'RES001'), 'read_content')


def test_registry_uses_validated_server_configuration_and_sanitizes_health():
    class _UnhealthySource(_Source):
        def health(self):
            raise RuntimeError('postgres://user:secret@host/db')

    source = _UnhealthySource('school-a')
    settings = type('Settings', (), {
        'source_registry_config': [{
            'source_id': 'SRC_A', 'backend': 'postgresql', 'namespace': 'school-a',
            'adapter_type': 'test', 'connection_ref': 'env:SOURCE_A_URL', 'enabled': True,
        }],
        'source_replay_seconds': 300,
    })()
    registry = create_source_registry(settings, source)
    assert registry.adapter_for_source_id('SRC_A') is source
    assert registry.health('SRC_A') == {
        'source_id': 'SRC_A', 'status': 'UNAVAILABLE', 'safe_error_code': 'SOURCE_UNAVAILABLE'}
    assert 'secret' not in str(registry.health('SRC_A'))


def test_master_sync_rejects_source_selection_when_federation_is_disabled():
    from types import SimpleNamespace

    from data_sync_etl.application.sync import MasterSync

    sync = MasterSync(SimpleNamespace(), SimpleNamespace(), federation_enabled=False)
    with pytest.raises(DomainError, match='SOURCE_FEDERATION_DISABLED'):
        sync.run('resources', source_id='UNTRUSTED_CLIENT_VALUE')


def test_automatic_sync_attempts_other_sources_after_one_fails():
    class _Coordinator:
        worker_stale_seconds = lease_seconds = 3

        def heartbeat_and_claim(self, _):
            return []

    class _Sync:
        federation_enabled = True

        def __init__(self):
            self.source_registry = type('Registry', (), {
                'registrations': lambda _: (_registration('SRC_A', 'school-a'),
                                            _registration('SRC_B', 'school-b')),
            })()
            self.calls = []

        def run(self, stream, *, full, source_id):
            self.calls.append((stream, source_id))
            if source_id == 'SRC_A':
                raise RuntimeError('SOURCE A unavailable')
            return {'ok': True}

    sync = _Sync()
    worker = MasterSyncWorker(sync, _Coordinator())
    with pytest.raises(RuntimeError, match='FEDERATED_SOURCE_SYNC_FAILED'):
        worker._run_stream('resources', full=False)
    assert sync.calls == [('resources', 'SRC_A'), ('resources', 'SRC_B')]


def test_router_rejects_mapping_whose_source_id_disagrees_with_registry():
    class _WrongSourceMapping:
        def resource_mapping(self, master_id):
            return {'master_resource_id': master_id, 'source_id': 'SRC_B',
                    'identity_version': locator.version, 'backend': locator.backend,
                    'namespace': locator.namespace, 'entity_type': locator.entity_type,
                    'source_record_key': locator.source_record_id,
                    'identity_hash': source_identity_hash(locator), 'status': 'ACTIVE'}

    locator = SourceLocator(backend='postgresql', namespace='school-a',
                            entity_type='resources', source_record_id='RES001')
    registry = ConfiguredSourceRegistry([(_registration('SRC_A', 'school-a'), _Source('school-a')),
                                         (_registration('SRC_B', 'school-b'), _Source('school-b'))])
    resource = {'id': 'MASTER_A', 'source_identity_version': locator.version,
                'source_backend': locator.backend, 'source_namespace': locator.namespace,
                'source_entity_type': locator.entity_type, 'source_record_key': locator.source_record_id,
                'source_identity_hash': source_identity_hash(locator)}
    with pytest.raises(DomainError, match='BLOCKED_SOURCE_MAPPING'):
        SourceRouter(registry, _WrongSourceMapping()).route_resource(resource)


def test_persisted_mapping_routes_colliding_ids_across_restart(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    with container.uow() as repo:
        original = repo.get('master_learning_resources', 'resource-1')
        second = deepcopy(original)

    locator_a = SourceLocator(backend='postgresql', namespace='school-a',
                              entity_type='resources', source_record_id='RES001')
    locator_b = SourceLocator(backend='postgresql', namespace='school-b',
                              entity_type='resources', source_record_id='RES001')
    with container.uow() as repo:
        repo.update('master_learning_resources', original['id'], {
            'source_identity_version': locator_a.version, 'source_backend': locator_a.backend,
            'source_namespace': locator_a.namespace, 'source_entity_type': locator_a.entity_type,
            'source_record_key': locator_a.source_record_id,
            'source_identity_hash': source_identity_hash(locator_a),
        })
        second.update({
            'id': 'MASTER_RESOURCE_B', 'title': 'Independent B resource',
            'source_identity_version': locator_b.version, 'source_backend': locator_b.backend,
            'source_namespace': locator_b.namespace, 'source_entity_type': locator_b.entity_type,
            'source_record_key': locator_b.source_record_id,
            'source_identity_hash': source_identity_hash(locator_b),
        })
        second['source_state_hash'] = state_hash(second)
        repo.insert('master_learning_resources', second)

    registry = ConfiguredSourceRegistry([(_registration('SRC_A', 'school-a'), _Source('school-a')),
                                         (_registration('SRC_B', 'school-b'), _Source('school-b'))])
    store = SQLAlchemySourceIdentityMappingStore(container.uow, registry)
    store.link_resource(source_id='SRC_A', locator=locator_a, master_resource_id=original['id'])
    store.link_resource(source_id='SRC_B', locator=locator_b, master_resource_id='MASTER_RESOURCE_B')

    # A new store instance simulates an application restart; mappings come from
    # the isolated MASTER test database, not process-local state.
    restarted_store = SQLAlchemySourceIdentityMappingStore(container.uow, registry)
    router = SourceRouter(registry, restarted_store)
    with container.uow() as repo:
        resource_a = repo.get('master_learning_resources', original['id'])
        resource_b = repo.get('master_learning_resources', 'MASTER_RESOURCE_B')
    assert router.route_resource(resource_a).adapter.namespace == 'school-a'
    assert router.route_resource(resource_b).adapter.namespace == 'school-b'

    with pytest.raises(DomainError, match='MASTER_MAPPING_IDENTITY_MISMATCH'):
        store.link_resource(source_id='SRC_A', locator=locator_a, master_resource_id='MASTER_RESOURCE_B')


def test_federation_backfills_verified_legacy_identity_without_changing_master_ids(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    with container.uow() as repo:
        legacy_ids = {stream: [row['id'] for row in repo.find(table)]
                      for stream, table in {
                          'grades': 'master_grade_levels', 'subjects': 'subjects',
                          'users': 'master_users', 'resources': 'master_learning_resources',
                      }.items()}
    source_id = 'SRC_LEGACY'
    registration = SourceRegistration(
        source_id=source_id, backend=container.source.source_backend,
        namespace=container.source.source_namespace, adapter_type='legacy-fixture', enabled=True)
    registry = ConfiguredSourceRegistry([(registration, container.source)])
    federated = MasterSync(container.source, container.uow, source_registry=registry,
                           federation_enabled=True)
    result = federated.run(full=True, source_id=source_id)
    assert all(stream['inserted'] == 0 for stream in result.values())
    with container.uow() as repo:
        assert legacy_ids == {stream: [row['id'] for row in repo.find(table)]
                              for stream, table in {
                                  'grades': 'master_grade_levels', 'subjects': 'subjects',
                                  'users': 'master_users', 'resources': 'master_learning_resources',
                              }.items()}
        assert all(row['source_identity_hash'] for table in (
            'master_grade_levels', 'subjects', 'master_users', 'master_learning_resources')
            for row in repo.find(table))


def test_federated_master_sync_is_idempotent_namespaces_relations_and_checkpoints(container, tmp_path):
    class _FixtureSource(JsonFixtureSourceAdapter):
        source_backend = 'postgresql'
        source_origin = 'POSTGRESQL'

        def __init__(self, root, namespace):
            super().__init__(root)
            self.source_namespace = namespace

    source_a = _FixtureSource(container.settings.source_fixture_root, 'school-a')
    root_b = tmp_path / 'source-b'
    shutil.copytree(container.settings.source_fixture_root, root_b)
    users_path = root_b / 'users.json'
    users = json.loads(users_path.read_text(encoding='utf-8'))
    for user in users:
        user['email'] = f"b-{user['email']}"
        if user.get('mongo_id'):
            user['mongo_id'] = f"b-{user['mongo_id']}"
    users_path.write_text(json.dumps(users), encoding='utf-8')
    grades_path = root_b / 'grades.json'
    grades = json.loads(grades_path.read_text(encoding='utf-8'))
    for grade in grades:
        if grade.get('mongo_id'):
            grade['mongo_id'] = f"{grade['mongo_id'][:-2]}bb"
    grades_path.write_text(json.dumps(grades), encoding='utf-8')
    resources_path = root_b / 'resources.json'
    resources = json.loads(resources_path.read_text(encoding='utf-8'))
    for resource in resources:
        resource['title'] = f"B - {resource['title']}"
        if resource.get('mongo_id'):
            resource['mongo_id'] = f"{resource['mongo_id'][:-2]}bb"
    resources_path.write_text(json.dumps(resources), encoding='utf-8')
    source_b = _FixtureSource(root_b, 'school-b')
    registry = ConfiguredSourceRegistry([(_registration('SRC_A', 'school-a'), source_a),
                                         (_registration('SRC_B', 'school-b'), source_b)])
    sync = MasterSync(source_a, container.uow, batch_size=100, source_registry=registry,
                      federation_enabled=True)
    before_tables = set(inspect(container.engine).get_table_names())

    result_a = sync.run(full=True, source_id='SRC_A')
    result_b = sync.run(full=True, source_id='SRC_B')
    assert set(inspect(container.engine).get_table_names()) == before_tables
    assert all(result['inserted'] for result in result_a.values())
    assert all(result['inserted'] for result in result_b.values())

    with container.uow() as repo:
        resources = repo.find('master_learning_resources')
        a_rows = [row for row in resources if row['source_namespace'] == 'school-a']
        b_rows = [row for row in resources if row['source_namespace'] == 'school-b']
        assert a_rows and b_rows and len(a_rows) == len(b_rows)
        assert {row['id'] for row in a_rows}.isdisjoint({row['id'] for row in b_rows})
        assert all(row['id'] != row['source_record_key'] for row in a_rows + b_rows)
        for row in a_rows + b_rows:
            relations = repo.find('resource_subjects', {'resource_id': row['id']})
            assert relations
            assert all(repo.get('subjects', relation['subject_id']) is not None for relation in relations)
        checkpoints = list(repo.session.query(SyncCheckpoint).all())
        assert {row.stream_name for row in checkpoints} == {
            f'{source_id}:{stream}' for source_id in ('SRC_A', 'SRC_B')
            for stream in ('grades', 'subjects', 'users', 'resources')}
        a_title = next(row['title'] for row in a_rows if row['source_record_key'] == 'resource-1')
        b_title = next(row['title'] for row in b_rows if row['source_record_key'] == 'resource-1')
        assert a_title != b_title
    listed = container.resource_listing.run(page=1, size=100)['items']
    listed_ids = {item['id'] for item in listed}
    assert {row['id'] for row in a_rows + b_rows} <= listed_ids

    repeat_b = sync.run(full=True, source_id='SRC_B')
    assert all(result['inserted'] == 0 for result in repeat_b.values())
    assert all(result['skipped'] for result in repeat_b.values())

    b_resources = json.loads(resources_path.read_text(encoding='utf-8'))
    for resource in b_resources:
        if resource['id'] == 'resource-1':
            resource['title'] = 'B updated title'
            resource['updated_at'] = '2026-10-30T00:00:00Z'
    resources_path.write_text(json.dumps(b_resources), encoding='utf-8')
    update_b = sync.run('resources', full=True, source_id='SRC_B')
    assert update_b['resources']['updated'] >= 1
    with container.uow() as repo:
        rows = repo.find('master_learning_resources', {'source_record_key': 'resource-1'})
        assert {row['source_namespace']: row['title'] for row in rows} == {
            'school-a': next(row['title'] for row in a_rows if row['source_record_key'] == 'resource-1'),
            'school-b': 'B updated title',
        }
        b_checkpoint = repo.source_checkpoint('SRC_B', 'resources')

    class _UnavailableSource:
        def __init__(self, delegate):
            self.delegate = delegate

        def locator(self, entity_type, source_record_id):
            return self.delegate.locator(entity_type, source_record_id)

        def batches(self, *args, **kwargs):
            raise RuntimeError('private connection error')

    failure_registry = ConfiguredSourceRegistry([
        (_registration('SRC_A', 'school-a'), _UnavailableSource(source_a)),
        (_registration('SRC_B', 'school-b'), source_b),
    ])
    failed_sync = MasterSync(source_a, container.uow, source_registry=failure_registry,
                             federation_enabled=True)
    with pytest.raises(DomainError, match='Sync failed'):
        failed_sync.run('resources', full=True, source_id='SRC_A')
    with container.uow() as repo:
        assert repo.source_checkpoint('SRC_B', 'resources') == b_checkpoint


def test_federated_reconciliation_soft_deletes_only_the_selected_source(container, tmp_path):
    class _FixtureSource(JsonFixtureSourceAdapter):
        source_backend = 'postgresql'
        source_origin = 'POSTGRESQL'

        def __init__(self, root, namespace):
            super().__init__(root)
            self.source_namespace = namespace

        @contextmanager
        def snapshot(self):
            yield _Reader(self)

    class _Reader:
        def __init__(self, source):
            self.source = source

        def reconciliation_summary(self, stream):
            ids = sorted(row.id for row in self.source.records(stream))
            return {'count': len(ids), 'minimum': ids[0] if ids else None,
                    'maximum': ids[-1] if ids else None}

        def reconciliation_ids(self, stream, after, size):
            ids = sorted(row.id for row in self.source.records(stream) if after is None or row.id > after)
            return ids[:size]

        def reconciliation_present(self, stream, ids):
            return {row.id for row in self.source.records(stream) if row.id in ids}

    source_a = _FixtureSource(container.settings.source_fixture_root, 'school-a')
    root_b = tmp_path / 'source-b'
    shutil.copytree(container.settings.source_fixture_root, root_b)
    resources_path = root_b / 'resources.json'
    resources = json.loads(resources_path.read_text(encoding='utf-8'))
    source_b = _FixtureSource(root_b, 'school-b')
    registry = ConfiguredSourceRegistry([(_registration('SRC_A', 'school-a'), source_a),
                                         (_registration('SRC_B', 'school-b'), source_b)])
    sync = MasterSync(source_a, container.uow, source_registry=registry, federation_enabled=True)
    sync.run(full=True, source_id='SRC_A')
    sync.run(full=True, source_id='SRC_B')

    with container.uow() as repo:
        a_id = repo.federated_master_id('SRC_A', source_a.locator('resources', 'resource-1'))
        b_id = repo.federated_master_id('SRC_B', source_b.locator('resources', 'resource-1'))
    resources_path.write_text(json.dumps([row for row in resources if row['id'] != 'resource-1']), encoding='utf-8')
    reconcile = ReconcileMaster(source_a, container.uow, max_affected=5, max_ratio=0.5,
                                source_registry=registry, federation_enabled=True)
    preview = reconcile.run('resources', source_id='SRC_B')
    assert preview['status'] == 'RECONCILIATION_PREVIEW', preview
    assert preview['candidate_count'] == 1
    applied = reconcile.run('resources', source_id='SRC_B', apply=True,
                            fingerprint=preview['fingerprint'])
    assert applied['status'] == 'FEDERATED_RECONCILIATION_APPLIED'
    with container.uow() as repo:
        row_a = repo.get('master_learning_resources', a_id)
        row_b = repo.get('master_learning_resources', b_id)
        assert row_a['is_active'] is True and row_a['deleted_at'] is None
        assert row_b['is_active'] is False and row_b['deleted_at'] is not None
        assert row_b['source_missing_at'] is not None

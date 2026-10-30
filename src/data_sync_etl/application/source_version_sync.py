"""Namespaced, metadata-only SOURCE version inventory in existing MASTER tables."""

from hashlib import sha256

from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash

_METADATA_BUCKET = '__SOURCE_METADATA_ONLY__'


class SourceVersionSynchronizer:
    def __init__(self, uow, batch_size=100):
        self.uow, self.batch_size = uow, max(1, min(int(batch_size), 100))

    @staticmethod
    def _version_identity(source, source_id, source_version_id):
        if (not isinstance(source_id, str) or not 1 <= len(source_id) <= 30
                or not isinstance(source_version_id, str) or not 1 <= len(source_version_id) <= 30
                or not isinstance(getattr(source, 'source_backend', None), str)
                or not isinstance(getattr(source, 'source_namespace', None), str)):
            raise DomainError('BLOCKED_SOURCE_VERSION_ID')
        return SourceLocator(
            backend=source.source_backend, namespace=source.source_namespace,
            entity_type='versions', source_record_id=source_version_id)

    def _upsert_page(self, repo, source_id, source, items):
        counts = {'inserted': 0, 'updated': 0, 'unchanged': 0}
        for item in items:
            source_version_id = item.get('source_version_id')
            locator = self._version_identity(source, source_id, source_version_id)
            if (not isinstance(item.get('resource_id'), str) or not 1 <= len(item['resource_id']) <= 255
                    or not isinstance(item.get('source_hash'), str)
                    or len(item['source_hash']) != 64
                    or any(ch not in '0123456789abcdefABCDEF' for ch in item['source_hash'])):
                raise DomainError('BLOCKED_SOURCE_VERSION_IDENTITY')
            master_resource_id = repo.federated_master_id(
                source_id, source.locator('resources', item['resource_id']))
            master_resource = repo.get('master_learning_resources', master_resource_id)
            if master_resource is None or not master_resource.get('source_identity_hash'):
                raise DomainError('BLOCKED_SOURCE_MAPPING')
            source_hash = item['source_hash'].lower()
            version_hash = source_identity_hash(locator)
            row = repo.find('source_file_imports', {
                'source_system': source_id, 'source_version_id': source_version_id}, limit=2)
            if len(row) > 1:
                raise DomainError('SOURCE_VERSION_IDENTITY_COLLISION')
            existing = row[0] if row else None
            if existing:
                target = repo.get('resource_versions', existing.get('target_version_id'))
                options = (target or {}).get('processing_options') or {}
                stored_identity = options.get('source_version_identity')
                if (existing['resource_id'] != master_resource_id
                        or existing['source_version_number'] != item['source_version_number']
                        or existing['source_hash'].lower() != source_hash
                        or not isinstance(stored_identity, dict)
                        or stored_identity.get('source_id') != source_id
                        or stored_identity.get('identity_hash') != version_hash):
                    raise DomainError('SOURCE_VERSION_MUTATED')
                fields = {
                    'resource_title': item.get('resource_title'),
                    'source_lifecycle_status': item['source_lifecycle_status'],
                    'source_resource_active': bool(item.get('resource_active', True)
                                                    and item.get('resource_deleted_at') is None),
                    'source_updated_at': item['updated_at'],
                    'updated_at': now(),
                }
                restored = existing.get('last_error_code') == 'SOURCE_VERSION_MISSING'
                if restored:
                    fields.update(status='METADATA_ONLY', last_error_code='SOURCE_METADATA_ONLY')
                repo.update('source_file_imports', existing['id'], fields)
                if restored and target:
                    repo.update('resource_versions', target['id'], {'lifecycle_status': 'METADATA_ONLY'})
                    counts['updated'] += 1
                else:
                    counts['unchanged'] += 1
                continue
            if (type(item.get('source_version_number')) is not int or item['source_version_number'] < 1
                    or len(str(item.get('source_lifecycle_status', ''))) > 30
                    or item.get('updated_at') is None):
                raise DomainError('BLOCKED_SOURCE_VERSION_METADATA')
            parent_id = item.get('parent_version_id')
            parent = None
            if parent_id:
                if not isinstance(parent_id, str) or not 1 <= len(parent_id) <= 30:
                    raise DomainError('BLOCKED_SOURCE_VERSION_PARENT')
                parent_rows = repo.find('source_file_imports', {
                    'source_system': source_id, 'source_version_id': parent_id}, limit=2)
                if len(parent_rows) > 1:
                    raise DomainError('SOURCE_VERSION_IDENTITY_COLLISION')
                if parent_rows and parent_rows[0].get('resource_id') == master_resource_id:
                    parent = parent_rows[0].get('target_version_id')
            key = sha256(f'{source_id}\0{source_version_id}'.encode()).hexdigest()
            options = {
                'content_availability': 'METADATA_ONLY',
                'source_version_identity': {
                    'source_id': source_id,
                    'backend': locator.backend,
                    'namespace': locator.namespace,
                    'source_resource_id': item['resource_id'],
                    'source_version_id': source_version_id,
                    'source_parent_version_id': parent_id,
                    'identity_hash': version_hash,
                },
            }
            version = repo.insert('resource_versions', {
                'id': new_id(), 'resource_id': master_resource_id,
                'parent_version_id': parent,
                'version_number': item['source_version_number'],
                'lifecycle_status': 'METADATA_ONLY',
                'storage_bucket': _METADATA_BUCKET,
                'storage_object_key': f'{_METADATA_BUCKET}/{key}',
                'source_hash': source_hash,
                'source_file_size': item.get('source_file_size'),
                'processing_options': options,
            })
            repo.insert('source_file_imports', {
                'id': new_id(), 'source_system': source_id,
                'source_version_id': source_version_id, 'resource_id': master_resource_id,
                'resource_title': item.get('resource_title'),
                'source_version_number': item['source_version_number'],
                'source_lifecycle_status': item['source_lifecycle_status'],
                'source_resource_active': bool(item.get('resource_active', True)
                                                and item.get('resource_deleted_at') is None),
                'source_updated_at': item['updated_at'],
                'storage_bucket': _METADATA_BUCKET,
                'storage_object_key': f'{_METADATA_BUCKET}/{key}',
                'source_hash': source_hash, 'source_file_size': item.get('source_file_size'),
                'status': 'METADATA_ONLY', 'last_error_code': 'SOURCE_METADATA_ONLY',
                'target_version_id': version['id'],
            })
            counts['inserted'] += 1
        return counts

    def sync(self, source_id, source):
        if not callable(getattr(source, 'version_page', None)):
            return {'status': 'UNSUPPORTED', 'inserted': 0, 'updated': 0, 'unchanged': 0}
        checkpoint_name = f'ver:{source_id}'
        if len(checkpoint_name.encode('utf-8')) > 30:
            raise DomainError('BLOCKED_SOURCE_CHECKPOINT_KEY')
        with self.uow() as repo:
            repo.ensure_checkpoint(checkpoint_name)
            checkpoint = repo.get('sync_checkpoints', checkpoint_name)
        cursor = checkpoint.get('last_id') if checkpoint else None
        totals = {'inserted': 0, 'updated': 0, 'unchanged': 0}
        while True:
            page = source.version_page(after=cursor, size=self.batch_size)
            items = page.get('items') if isinstance(page, dict) else None
            if not isinstance(items, list) or len(items) > self.batch_size:
                raise DomainError('BLOCKED_SOURCE_VERSION_PAGE')
            with self.uow() as repo:
                counts = self._upsert_page(repo, source_id, source, items)
                totals = {key: totals[key] + counts[key] for key in totals}
                next_cursor = page.get('next_cursor')
                if next_cursor is not None and (not items or next_cursor == cursor or
                                                len(next_cursor) > 30):
                    raise DomainError('BLOCKED_SOURCE_VERSION_CURSOR')
                repo.update('sync_checkpoints', checkpoint_name, {
                    'last_id': next_cursor, 'updated_at': now(),
                })
            if next_cursor is None:
                break
            cursor = next_cursor
        # Attach parent links only after the source inventory has been fully mapped.
        cursor = None
        while True:
            page = source.version_page(after=cursor, size=self.batch_size)
            items = page['items']
            with self.uow() as repo:
                for item in items:
                    parent_source_id = item.get('parent_version_id')
                    if not parent_source_id:
                        continue
                    child = repo.find('source_file_imports', {
                        'source_system': source_id, 'source_version_id': item['source_version_id']}, limit=1)
                    parent = repo.find('source_file_imports', {
                        'source_system': source_id, 'source_version_id': parent_source_id}, limit=1)
                    if (not child or not parent or child[0]['resource_id'] != parent[0]['resource_id']
                            or parent[0]['status'] not in ('METADATA_ONLY', 'IMPORTED', 'PROCESSING')):
                        if child and child[0].get('status') != 'FAILED':
                            repo.update('source_file_imports', child[0]['id'], {
                                'status': 'FAILED', 'source_resource_active': False,
                                'last_error_code': 'SOURCE_PARENT_VERSION_MISSING', 'updated_at': now(),
                            })
                            if child[0].get('target_version_id'):
                                child_version = repo.get('resource_versions', child[0]['target_version_id'])
                                if child_version is not None:
                                    repo.update('resource_versions', child_version['id'], {
                                        'lifecycle_status': 'REVOKED',
                                    })
                        continue
                    child_version = repo.get('resource_versions', child[0]['target_version_id'])
                    parent_version = repo.get('resource_versions', parent[0]['target_version_id'])
                    if child_version and parent_version and child_version['parent_version_id'] != parent_version['id']:
                        repo.update('resource_versions', child_version['id'], {
                            'parent_version_id': parent_version['id'],
                        })
                    if (child[0].get('last_error_code') == 'SOURCE_PARENT_VERSION_MISSING'
                            and child_version and parent_version):
                        repo.update('source_file_imports', child[0]['id'], {
                            'status': 'METADATA_ONLY',
                            'source_resource_active': bool(item.get('resource_active', True)
                                                           and item.get('resource_deleted_at') is None),
                            'last_error_code': 'SOURCE_METADATA_ONLY', 'updated_at': now(),
                        })
                        if child_version['lifecycle_status'] == 'REVOKED':
                            repo.update('resource_versions', child_version['id'], {
                                'lifecycle_status': 'METADATA_ONLY',
                            })
            cursor = page.get('next_cursor')
            if cursor is None:
                break
        self._reconcile_missing(source_id, source)
        with self.uow() as repo:
            repo.update('sync_checkpoints', checkpoint_name, {'last_id': None, 'updated_at': now()})
        return {'status': 'COMPLETED', **totals}

    def _source_version_ids(self, source):
        after = None
        previous = None
        while True:
            page = source.version_page(after=after, size=self.batch_size)
            items = page.get('items') if isinstance(page, dict) else None
            if not isinstance(items, list) or len(items) > self.batch_size:
                raise DomainError('BLOCKED_SOURCE_VERSION_PAGE')
            for item in items:
                value = item.get('source_version_id') if isinstance(item, dict) else None
                if (not isinstance(value, str) or not 1 <= len(value) <= 30
                        or previous is not None and value.encode('utf-8') <= previous.encode('utf-8')):
                    raise DomainError('BLOCKED_SOURCE_VERSION_ORDER')
                previous = value
                yield value
            next_cursor = page.get('next_cursor')
            if next_cursor is None:
                break
            if not items or next_cursor != items[-1].get('source_version_id') or next_cursor == after:
                raise DomainError('BLOCKED_SOURCE_VERSION_CURSOR')
            after = next_cursor

    def _reconcile_missing(self, source_id, source):
        current = iter(self._source_version_ids(source))
        source_version_id = next(current, None)
        master_after = None
        while True:
            with self.uow() as repo:
                rows = repo.source_imports_by_system_page(source_id, master_after, self.batch_size)
                for row in rows:
                    wanted = row['source_version_id']
                    while (source_version_id is not None
                           and source_version_id.encode('utf-8') < wanted.encode('utf-8')):
                        source_version_id = next(current, None)
                    if source_version_id == wanted:
                        source_version_id = next(current, None)
                        continue
                    if row.get('status') == 'FAILED' and row.get('last_error_code') == 'SOURCE_VERSION_MISSING':
                        continue
                    repo.update('source_file_imports', row['id'], {
                        'status': 'FAILED', 'source_resource_active': False,
                        'last_error_code': 'SOURCE_VERSION_MISSING', 'updated_at': now(),
                    })
                    if row.get('target_version_id'):
                        target = repo.get('resource_versions', row['target_version_id'])
                        if target is not None:
                            repo.update('resource_versions', target['id'], {'lifecycle_status': 'REVOKED'})
            if not rows:
                break
            master_after = rows[-1]['source_version_id']

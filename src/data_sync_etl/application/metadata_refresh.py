"""Opt-in bounded sweep of committed MASTER metadata using the existing worker.

Checkpoint is durable in sync_checkpoints. RPC/embedding happens between short
checkpoint transactions; a crash replays the same idempotent resource batch.
"""
from time import monotonic

from data_sync_etl.domain.core import now
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.logging import event

CHECKPOINT = 'metadata-search-refresh-v1'


class MetadataRefresh:
    def __init__(self, service, settings, semantic=None):
        self.service, self.settings = service, settings
        self.semantic = semantic
        self.next_due, self.failures = 0., 0

    def tick(self):
        if (not self.settings.search_metadata_enabled or not self.settings.search_metadata_auto_refresh_enabled
                or self.service is None or monotonic() < self.next_due):
            return {'status': 'DISABLED_OR_WAITING'}
        report = {'status': 'READY', 'processed': 0, 'writes': 0, 'embeds': 0}
        started = monotonic()
        try:
            with self.service.uow.metadata_projection_lock(CHECKPOINT) as acquired:
                if not acquired:
                    return {'status': 'BUSY'}
                # A missing collection is a provisioning blocker, never auto-create it.
                self.service.store.ensure_ready()
                if callable(getattr(type(self.service), 'propagate_deletions', None)):
                    report['deletion_events'] = self.service.propagate_deletions(
                        self.settings.search_metadata_refresh_batch_size)
                with self.service.uow() as repo:
                    repo.ensure_checkpoint(CHECKPOINT)
                    checkpoint = repo.get('sync_checkpoints', CHECKPOINT)
                    ids = repo.metadata_refresh_page(checkpoint['last_id'], self.settings.search_metadata_refresh_batch_size)
                for resource_id in ids:
                    result = self.service.reconcile({'resource_id': resource_id}, apply=True,
                                                   dense=self.settings.search_metadata_refresh_dense)
                    if result['status'] != 'READY':
                        raise RuntimeError('METADATA_REFRESH_PARTIAL')
                    if self.settings.search_metadata_refresh_content_semantic and self.settings.semantic_enabled and self.semantic is not None:
                        from data_sync_etl.domain.search_access import SearchAccess
                        snapshots = self.service.snapshots({'resource_id': resource_id}, SearchAccess('authenticated_admin'))
                        for version_id in snapshots:
                            self.semantic.refresh_existing(version_id)
                    with self.service.uow() as repo:
                        repo.update('sync_checkpoints', CHECKPOINT, {'last_id': resource_id, 'updated_at': now()})
                    report['processed'] += 1
                    report['writes'] += result['writes']
                    report['embeds'] += result['embeds']
                if len(ids) < self.settings.search_metadata_refresh_batch_size:
                    with self.service.uow() as repo:
                        repo.update('sync_checkpoints', CHECKPOINT, {'last_id': None, 'updated_at': now(), 'last_updated_at': now()})
                    report['sweep_complete'] = True
                self.failures = 0
                self.next_due = monotonic() + self.settings.search_metadata_refresh_interval_seconds
        except Exception as exc:
            self.failures += 1
            self.next_due = monotonic() + min(300, 5 * 2 ** min(self.failures - 1, 6))
            report['status'] = 'RETRY_REQUIRED'
            report['error_code'] = exc.code if isinstance(exc, IndexingError) else 'METADATA_REFRESH_FAILED'
            report['backoff_seconds'] = min(300, 5 * 2 ** min(self.failures - 1, 6))
        report.update(batch_seconds=monotonic() - started, consecutive_failures=self.failures)
        event(stage='metadata_search_refresh', action=report['status'], coverage=report)
        return report

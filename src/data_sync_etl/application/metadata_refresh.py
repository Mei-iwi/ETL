"""Opt-in bounded sweep of committed MASTER metadata using the existing worker.

Checkpoint is durable in sync_checkpoints. RPC/embedding happens between short
checkpoint transactions; a crash replays the same idempotent resource batch.
"""
from time import monotonic

from data_sync_etl.domain.core import now
from data_sync_etl.logging import event

CHECKPOINT = 'metadata-search-refresh-v1'


class MetadataRefresh:
    def __init__(self, service, settings):
        self.service, self.settings = service, settings
        self.next_due, self.failures = 0., 0

    def tick(self):
        if (not self.settings.search_metadata_enabled or not self.settings.search_metadata_auto_refresh_enabled
                or self.service is None or monotonic() < self.next_due):
            return {'status': 'DISABLED_OR_WAITING'}
        report = {'status': 'READY', 'processed': 0, 'writes': 0, 'embeds': 0}
        try:
            with self.service.uow.metadata_projection_lock(CHECKPOINT) as acquired:
                if not acquired:
                    return {'status': 'BUSY'}
                # A missing collection is a provisioning blocker, never auto-create it.
                self.service.store.ensure_ready()
                with self.service.uow() as repo:
                    repo.ensure_checkpoint(CHECKPOINT)
                    checkpoint = repo.get('sync_checkpoints', CHECKPOINT)
                    ids = repo.metadata_refresh_page(checkpoint['last_id'], self.settings.search_metadata_refresh_batch_size)
                for resource_id in ids:
                    result = self.service.reconcile({'resource_id': resource_id}, apply=True,
                                                   dense=self.settings.search_metadata_refresh_dense)
                    if result['status'] != 'READY':
                        raise RuntimeError('METADATA_REFRESH_PARTIAL')
                    with self.service.uow() as repo:
                        repo.update('sync_checkpoints', CHECKPOINT, {'last_id': resource_id, 'updated_at': now()})
                    report['processed'] += 1
                    report['writes'] += result['writes']
                    report['embeds'] += result['embeds']
                if len(ids) < self.settings.search_metadata_refresh_batch_size:
                    with self.service.uow() as repo:
                        repo.update('sync_checkpoints', CHECKPOINT, {'last_id': None, 'updated_at': now()})
                self.failures = 0
                self.next_due = monotonic() + self.settings.search_metadata_refresh_interval_seconds
        except Exception:
            self.failures += 1
            self.next_due = monotonic() + min(300, 5 * 2 ** min(self.failures - 1, 6))
            report['status'] = 'RETRY_REQUIRED'
        event(stage='metadata_search_refresh', action=report['status'], coverage=report)
        return report

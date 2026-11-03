"""Keep federated MASTER identities fresh through ordinary incremental sync."""
from data_sync_etl.domain.core import DomainError, SyncAlreadyRunning
from data_sync_etl.logging import event, flow
from data_sync_etl.workers.lifecycle import tick, worker_lifecycle


class UsersRefreshWorker:
    def __init__(self, sync, settings, *, source_id=None, interval_seconds=None):
        if settings.auth_mode != 'master_users':
            raise DomainError('USERS_REFRESH_REQUIRES_MASTER_AUTH')
        if sync.federation_enabled:
            if not source_id:
                raise DomainError('SOURCE_ID_REQUIRED')
            sync.source_registry.adapter_for_source_id(source_id, 'read_metadata')
        elif source_id is not None:
            raise DomainError('SOURCE_FEDERATION_DISABLED')
        freshness = settings.auth_master_freshness_seconds
        interval = interval_seconds if interval_seconds is not None else max(5, freshness // 3)
        if type(interval) is not int or not 1 <= interval <= freshness // 2:
            raise DomainError('USERS_REFRESH_INTERVAL_INVALID')
        self.sync, self.source_id, self.interval_seconds = sync, source_id, interval

    def once(self):
        with flow():
            try:
                options = {'source_id': self.source_id} if self.source_id is not None else {}
                result = self.sync.run('users', full=False, **options)
                users = result.get('users') or {}
                succeeded = (users.get('status', 'COMPLETED') == 'COMPLETED'
                             and bool(users.get('run_id') or users.get('status') == 'COMPLETED'))
                status = 'COMPLETED' if succeeded else 'FAILED'
            except SyncAlreadyRunning:
                status = 'BUSY'
            except Exception:
                # Never serialize exceptions, credentials, rows or password hashes.
                status = 'FAILED'
            event(stage='users_refresh', action=status)
            return {'status': status}

    def run(self, *, once=False):
        if once:
            return self.once()
        with worker_lifecycle() as stop:
            while not stop.is_set():
                tick()
                self.once()
                stop.wait(self.interval_seconds)
        return {'status': 'STOPPED'}

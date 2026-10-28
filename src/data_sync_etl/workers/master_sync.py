from threading import Event, Thread
from time import monotonic

from data_sync_etl.domain.core import SyncAlreadyRunning
from data_sync_etl.logging import event, flow
from data_sync_etl.workers.lifecycle import tick, worker_lifecycle


class MasterSyncWorker:
    def __init__(self, sync, coordinator, poll_seconds=5, metadata_refresh=None, auth_freshness_seconds=None):
        self.sync = sync
        self.coordinator = coordinator
        self.poll_seconds = poll_seconds
        self.metadata_refresh = metadata_refresh
        self.auth_freshness_seconds = auth_freshness_seconds

    def refresh_metadata(self):
        if self.metadata_refresh is not None:
            self.metadata_refresh.tick()

    def once(self, worker_id: str):
        with flow():
            streams = self.coordinator.heartbeat_and_claim(worker_id)
            if not streams:
                self.refresh_metadata()
                return {"action": "IDLE"}
            refresh_users = self.auth_freshness_seconds is not None and 'users' in streams
            if refresh_users:
                streams = ['users', *(stream for stream in streams if stream != 'users')]
            else:
                streams = sorted(streams, key=lambda name: ('grades', 'subjects', 'users', 'resources').index(name))

            stop = Event()
            heartbeat_seconds = max(
                1,
                min(
                    self.coordinator.worker_stale_seconds,
                    self.coordinator.lease_seconds,
                )
                // 3,
            )

            def heartbeat():
                while not stop.wait(heartbeat_seconds):
                    if not self.coordinator.renew(worker_id):
                        return

            thread = Thread(target=heartbeat, daemon=True)
            thread.start()
            error_code = None
            try:
                action = "COMPLETED"
                users_started, extra_users_refresh = None, False
                for stream in streams:
                    try:
                        started = monotonic()
                        result = self.sync.run(stream, full=self.coordinator.mode(stream) == "FULL")
                        if isinstance(result, dict) and result.get('status') in ('FAILED', 'RETRY_WAIT', 'BLOCKED'):
                            error_code, action = 'SYNC_FAILED', 'FAILED'
                        if stream == 'users':
                            users_started = started
                        elif (refresh_users and not extra_users_refresh and users_started is not None
                              and monotonic() - users_started >= self.auth_freshness_seconds / 2):
                            # Bound extra work to one genuine users sync per cycle.
                            # Never touch freshness timestamps without reading SOURCE
                            # and committing through the normal sync/checkpoint path.
                            extra_users_refresh = True
                            self.sync.run('users', full=self.coordinator.mode('users') == 'FULL')
                    except SyncAlreadyRunning:
                        error_code = "SYNC_BUSY"
                        action = "SKIPPED"
                        break
                    except Exception:
                        error_code = "SYNC_FAILED"
                        action = "FAILED"
            finally:
                stop.set()
                thread.join(timeout=5)
                self.coordinator.complete(worker_id, error_code)
            event(stage="master_sync", action=f"AUTO_{action}")
            self.refresh_metadata()
            return {"action": action}

    def run(self, worker_id: str, once=False):
        if once:
            try:
                return self.once(worker_id)
            finally:
                self.coordinator.mark_worker_stopped(worker_id)
        with worker_lifecycle() as stop:
            try:
                while not stop.is_set():
                    tick()
                    self.once(worker_id)
                    stop.wait(self.poll_seconds)
            except KeyboardInterrupt:
                return None
            finally:
                self.coordinator.mark_worker_stopped(worker_id)

from threading import Event, Thread

from data_sync_etl.domain.core import SyncAlreadyRunning
from data_sync_etl.logging import event, flow
from data_sync_etl.workers.lifecycle import tick, worker_lifecycle


class MasterSyncWorker:
    def __init__(self, sync, coordinator, poll_seconds=5):
        self.sync = sync
        self.coordinator = coordinator
        self.poll_seconds = poll_seconds

    def once(self, worker_id: str):
        with flow():
            streams = self.coordinator.heartbeat_and_claim(worker_id)
            if not streams:
                return {"action": "IDLE"}

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
                for stream in streams:
                    try:
                        self.sync.run(stream, full=self.coordinator.mode(stream) == "FULL")
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

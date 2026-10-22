from threading import Event, Thread

from data_sync_etl.logging import event, flow
from data_sync_etl.workers.lifecycle import tick, worker_lifecycle


class SourceFileImportWorker:
    def __init__(self, service, poll_seconds=5):
        self.service = service
        self.poll_seconds = poll_seconds

    def once(self, worker_id):
        if not worker_id or len(worker_id) > 100:
            raise ValueError("Worker ID must have 1 to 100 characters")
        with flow():
            try:
                self.service.discover_once()
            except Exception:
                event(stage="source_import", action="DISCOVERY_UNAVAILABLE")
            try:
                claimed = self.service.claim(worker_id)
            except Exception:
                event(stage="source_import", action="QUEUE_UNAVAILABLE")
                return False
            if claimed is None:
                return False
            stop = Event()

            def heartbeat():
                while not stop.wait(self.service.settings.source_file_import_lease_seconds / 3):
                    try:
                        self.service.heartbeat(claimed)
                    except Exception:
                        return

            thread = Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                self.service.execute(claimed)
            except Exception:
                # The lease expires on process/DB failure; recovery uses persisted mapping.
                event(stage="source_import", action="IMPORT_INTERRUPTED")
            finally:
                stop.set()
                thread.join(timeout=5)
            return True

    def run(self, worker_id, once=False):
        if once:
            try:
                return self.once(worker_id)
            finally:
                self.service.worker_stopped(worker_id)
        with worker_lifecycle() as stop:
            try:
                while not stop.is_set():
                    tick()
                    self.once(worker_id)
                    stop.wait(self.poll_seconds)
            except KeyboardInterrupt:
                return None
            finally:
                self.service.worker_stopped(worker_id)

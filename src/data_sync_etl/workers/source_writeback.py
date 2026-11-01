"""Restart-safe SOURCE write-back recovery worker.

The worker is intentionally hosted by the existing reconciliation process.  It
does not enable write-back, create grants, or bypass the writer contract; when
the capability is disabled it simply remains idle.
"""

from data_sync_etl.logging import event, flow


class SourceWriteBackWorker:
    def __init__(self, service, create_service=None, delete_service=None, version_service=None,
                 reconcile_source=None):
        self.service = service
        self.create_service = create_service
        self.delete_service = delete_service
        self.version_service = version_service
        self.reconcile_source = reconcile_source

    def once(self, worker_id='source-writeback-worker'):
        with flow():
            try:
                worked = self.service.process_pending_once(worker_id)
                if self.create_service is not None:
                    worked = self.create_service.process_pending_once(worker_id) or worked
                    if (getattr(self.create_service, 'last_source_committed', False)
                            and self.reconcile_source is not None):
                        try:
                            # SOURCE is already committed and idempotent.  A
                            # reconciliation failure must not repeat the write;
                            # the scheduled MasterSync worker will retry it.
                            self.reconcile_source()
                            mark_reconciled = getattr(self.create_service, 'mark_master_reconciled', None)
                            if callable(mark_reconciled):
                                mark_reconciled()
                        except Exception:
                            event(stage='source_writeback', action='SOURCE_CREATE_RECONCILIATION_PENDING')
                if self.delete_service is not None:
                    worked = self.delete_service.process_pending_once(worker_id) or worked
                if self.version_service is not None:
                    worked = self.version_service.process_pending_once(worker_id) or worked
            except Exception:
                event(stage='source_writeback', action='WRITEBACK_WORKER_UNAVAILABLE')
                return False
            if worked:
                event(stage='source_writeback', action='WRITEBACK_JOB_PROCESSED')
            return bool(worked)

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
                metadata_worked = self.service.process_pending_once(worker_id)
                worked = metadata_worked
                if metadata_worked and self.reconcile_source is not None:
                    try:
                        # SOURCE is canonical.  Read it back through the
                        # normal sync path after a committed write so MASTER
                        # never remains a stale local projection and the
                        # SOURCE->MASTER marker prevents a write-back loop.
                        self.reconcile_source()
                    except Exception:
                        event(stage='source_writeback', action='SOURCE_METADATA_RECONCILIATION_PENDING')
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
                    delete_worked = self.delete_service.process_pending_once(worker_id)
                    worked = delete_worked or worked
                    if delete_worked and self.reconcile_source is not None:
                        try:
                            self.reconcile_source()
                        except Exception:
                            event(stage='source_writeback', action='SOURCE_DELETE_RECONCILIATION_PENDING')
                if self.version_service is not None:
                    version_worked = self.version_service.process_pending_once(worker_id)
                    worked = version_worked or worked
                    if version_worked and self.reconcile_source is not None:
                        try:
                            self.reconcile_source()
                        except Exception:
                            event(stage='source_writeback', action='SOURCE_VERSION_RECONCILIATION_PENDING')
                worked = self._recover_reconciliation() or worked
            except Exception:
                event(stage='source_writeback', action='WRITEBACK_WORKER_UNAVAILABLE')
                return False
            if worked:
                event(stage='source_writeback', action='WRITEBACK_JOB_PROCESSED')
            return bool(worked)

    def _recover_reconciliation(self):
        """Recover SOURCE commit -> MASTER projection after process restarts."""
        if self.reconcile_source is None:
            return False
        uow = getattr(self.service, 'uow', None)
        if uow is None:
            return False
        with uow() as repo:
            reader = getattr(repo, 'pending_writeback_reconciliations', None)
            pending = reader() if callable(reader) else []
        if not pending:
            return False
        try:
            self.reconcile_source()
            for job in pending:
                if job['audit_type'] == 'SOURCE_CREATE' and self.create_service is not None:
                    self.create_service.last_request_id = job['id']
                    self.create_service.mark_master_reconciled()
                else:
                    with uow() as repo:
                        current = repo.get('reconciliation_audit_jobs', job['id'], lock=True)
                        cp = dict(current.get('checkpoint') or {})
                        cp['master_reconciliation_status'] = 'MASTER_RECONCILED'
                        repo.update('reconciliation_audit_jobs', job['id'], {'checkpoint': cp})
            return True
        except Exception:
            event(stage='source_writeback', action='SOURCE_WRITEBACK_RECONCILIATION_PENDING')
            return False

"""Construct background audit jobs through the UnitOfWork infrastructure port."""


class FileReconciliationJobs:
    def __new__(cls, engine):
        return engine.uow.file_reconciliation_jobs(engine)

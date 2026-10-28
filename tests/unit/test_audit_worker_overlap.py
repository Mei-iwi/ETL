"""Stream execution overlap; SQLite DB writes are serialized by the fixture."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationAuditJob as Job


def test_different_stream_jobs_overlap_while_same_stream_waits(container, monkeypatch):
    c = container
    c.settings.reconciliation_max_active = 2
    queue = AuditQueue(c.uow, c.settings)
    keys = [queue.enqueue('METADATA', stream) for stream in ('grades', 'subjects')]
    claims = {key: queue.claim(key) for key in keys}
    waiting = queue.enqueue('IDS', 'grades')
    assert queue.claim(waiting) is None
    barrier, database = Barrier(2), Lock()
    entered = []
    def metadata(self, claim):
        entered.append(claim['stream_name'])
        barrier.wait(timeout=3)  # Real overlap in ScheduledAudit.once, not two serial calls.
        with database:
            self._save(claim, {'records_scanned': 0}, status='COMPLETED')
    monkeypatch.setattr(ScheduledAudit, '_metadata', metadata)
    def work(key):
        audit = ScheduledAudit(c.reconciliation_engine, c.settings)
        # Claims were made with the real SQLite admission, sequentially. Actual
        # concurrent PostgreSQL claims are covered by the optional integration suite.
        monkeypatch.setattr(audit.queue, 'claim', lambda _: claims[key])
        return audit.once(key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(work, keys)) == [True, True]
    assert sorted(entered) == ['grades', 'subjects']
    with c.uow() as repo:
        assert all(repo.session.get(Job, key).status == 'COMPLETED' for key in keys)
    assert queue.claim(waiting) is not None

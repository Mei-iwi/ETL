"""Admin-triggered file audit with durable progress; no business/file mutations."""
from bisect import bisect_right
from collections import Counter
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace

from sqlalchemy import select

from data_sync_etl.db.models import (
    ResourceVersion,
    SyncReconciliationDiscrepancy,
    SyncReconciliationRun,
)
from data_sync_etl.domain.core import DomainError, new_id, now


class FileReconciliationJobs:
    def __init__(self, engine):
        self.engine = engine
        self.cancelled = Event()
        self.thread = None

    def start(self, stream='files', mode='INCREMENTAL'):
        if not self.engine._run_lock.acquire(blocking=False):
            raise DomainError('RECONCILIATION_BUSY')
        self.cancelled.clear()
        ready, state = Event(), {'error': None, 'initial': None}
        self.thread = Thread(target=self._run, args=(stream, ready, state, mode), daemon=True, name='file-reconciliation')
        try:
            self.thread.start()
        except Exception:
            self.engine._run_lock.release()
            raise
        if not ready.wait(10):
            self.cancelled.set()
            raise DomainError('RECONCILIATION_START_TIMEOUT')
        if state['error']:
            raise DomainError(state['error'])
        return {'status': 'RUNNING', 'executed_at': now().isoformat(), 'dry_run': True, 'mode': mode,
                'total_streams': 5 if stream == 'all' else 1, 'reports': [state['initial']]}

    def close(self):
        self.cancelled.set()
        if self.thread:
            self.thread.join(timeout=2)

    def can_recover(self):
        if self.thread and self.thread.is_alive():
            return False
        with self.engine.uow.processing_lock('reconciliation-files') as acquired:
            return acquired

    def _run(self, stream, ready, state, mode='INCREMENTAL'):
        run_id = None
        previous_seconds, previous_bytes = self.engine.file_read_budget_seconds, self.engine.file_max_bytes
        try:
            # Same UoW fence used for processing: one audit owner across API processes.
            with self.engine.uow.processing_lock('reconciliation-files') as acquired:
                if not acquired:
                    state['error'] = 'RECONCILIATION_BUSY'
                    return
                with self.engine.uow() as repo:
                    versions = list(repo.session.scalars(select(ResourceVersion.id).order_by(ResourceVersion.id).limit(10001)))
                    if len(versions) > 10000:
                        state['error'] = 'RECONCILIATION_LIMIT_EXCEEDED'
                        return
                    previous = repo.session.scalar(select(SyncReconciliationRun).where(
                        SyncReconciliationRun.stream_name == 'files').order_by(SyncReconciliationRun.started_at.desc()).limit(1))
                    resume = None
                    if previous and previous.status in ('RUNNING', 'PARTIAL'):
                        meta = previous.summary_meta or {}
                        if meta.get('processed_files', 0) < meta.get('total_files', 0):
                            resume = meta.get('last_processed_id')
                    # The global DB fence proves that these RUNNING rows have no owner.
                    for orphan in repo.session.scalars(select(SyncReconciliationRun).where(
                            SyncReconciliationRun.stream_name == 'files', SyncReconciliationRun.status == 'RUNNING')):
                        orphan.status, orphan.finished_at = 'PARTIAL', now()
                        orphan.summary_meta = {**(orphan.summary_meta or {}), 'reason': 'RECONCILIATION_INTERRUPTED'}
                    if resume:
                        split = bisect_right(versions, resume)
                        versions = versions[split:] + versions[:split]
                    run_id = new_id()
                    record = SyncReconciliationRun(run_id=run_id, stream_name='files', status='RUNNING',
                        started_at=now(), source_count=0, target_count=len(versions), matched_count=0,
                        mismatched_count=0, source_total_bytes=0, target_total_bytes=0,
                        summary_meta={'dry_run': True, 'mode': mode, 'processed_files': 0, 'total_files': len(versions),
                                      'resume_after_id': resume, 'unverified_source_files': 0})
                    repo.session.add(record)
                    repo.session.flush()
                    state['initial'] = dict(run_id=run_id, stream_name='files', status='RUNNING', mode=mode,
                        started_at=record.started_at.isoformat(), source_count=0, target_count=len(versions),
                        matched_count=0, mismatched_count=0, match_rate_percent=0, summary_meta=record.summary_meta)
                ready.set()  # SOURCE network I/O starts AFTER the accepted response is available.
                metadata_failed = False
                if stream == 'all':
                    for name in ('grades', 'subjects', 'users', 'resources'):
                        if self.cancelled.is_set():
                            break
                        try:
                            self.engine._execute_stream_reconcile(name, dry_run=True, mode=mode)
                        except Exception:
                            metadata_failed = True
                source = {'count': 0}
                try:
                    adapter = self.engine.source_adapter
                    if hasattr(adapter, 'engine'):
                        with adapter.engine.connect() as conn:
                            if conn.dialect.name == 'postgresql':
                                conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                                conn.exec_driver_sql("SET LOCAL statement_timeout = '5000'")
                            source = self.engine.compute_table_summary(conn, 'resource_versions', 'files')
                    elif hasattr(adapter, 'snapshot'):
                        with adapter.snapshot() as reader:
                            source = {'count': reader.reconciliation_summary('files').get('count', 0)}
                except Exception:
                    source = {'count': 0, 'error_code': 'SOURCE_SUMMARY_UNAVAILABLE'}
                self.engine.file_cancelled = self.cancelled
                counts, reasons, verified_bytes = Counter(), Counter(), 0
                settings = getattr(self.engine, 'audit_settings', None)
                deadline = monotonic() + getattr(settings, 'reconciliation_max_seconds_per_run', 1800)
                maximum = getattr(settings, 'reconciliation_max_bytes_per_run', previous_bytes)
                maximum_files = getattr(settings, 'reconciliation_max_files_per_run', 100)
                io_bytes, budget_exhausted = 0, False
                for ordinal, version_id in enumerate(versions, 1):
                    if self.cancelled.is_set():
                        break
                    if ordinal > maximum_files or monotonic() >= deadline or io_bytes >= maximum:
                        budget_exhausted = True
                        break
                    self.engine.file_read_budget_seconds = min(previous_seconds, deadline - monotonic())
                    self.engine.file_max_bytes = maximum - io_bytes
                    # Detach a single metadata record before SOURCE/network/hash I/O.
                    with self.engine.uow() as repo:
                        metadata = repo.get('resource_versions', version_id)
                        resource = repo.get('master_learning_resources', metadata['resource_id']) if metadata else None
                    rows = self.engine.verify_file_snapshot([SimpleNamespace(**metadata)],
                        {metadata['resource_id']: resource['source_origin'] if resource else None},
                        audit_type='LIGHT' if mode == 'INCREMENTAL' else 'DEEP') if metadata else []
                    io_bytes += self.engine.file_metrics.get('bytes_read', 0)
                    result = rows[0] if rows else {'discrepancy_flag': 'NOT_VERIFIED', 'source_value': None,
                        'target_value': {'reason_code': 'FILE_VERSION_MISSING'}}
                    flag = result['discrepancy_flag']
                    counts[flag] += 1
                    if flag == 'SYNCED':
                        value = result.get('target_value') or {}
                        verified_bytes += value.get('verified_size', value.get('storage_file_size', 0)) or 0
                    if flag == 'NOT_VERIFIED':
                        reasons[result['target_value'].get('reason_code', 'UNKNOWN')] += 1
                    with self.engine.uow() as repo:
                        record = repo.session.get(SyncReconciliationRun, run_id)
                        if record.status != 'RUNNING':
                            return  # Fenced/recovered by another owner; never overwrite its state.
                        record.source_count = source['count']
                        record.source_table_hash = source.get('table_fingerprint')
                        record.matched_count = counts['SYNCED']
                        record.mismatched_count = sum(n for key, n in counts.items() if key not in ('SYNCED', 'NOT_VERIFIED'))
                        record.summary_meta = {**record.summary_meta, 'processed_files': ordinal,
                            'last_processed_id': version_id, 'unverified_source_files': counts['NOT_VERIFIED'],
                            'verified_file_count': counts['SYNCED'], 'verification_reason_counts': dict(reasons),
                            'source_summary': source, 'metadata_failed': metadata_failed, 'verified_bytes': verified_bytes,
                            'bytes_read': io_bytes,
                            'file_verification_scope': 'MASTER_REFERENCED_VERSIONS'}
                        if flag != 'SYNCED':
                            repo.session.add(SyncReconciliationDiscrepancy(id=new_id(), run_id=run_id,
                                stream_name='files', record_id=version_id, discrepancy_flag=flag,
                                source_value=result.get('source_value'), target_value=result.get('target_value'),
                                detected_at=now(), resolution_status='UNRESOLVED'))
                with self.engine.uow() as repo:
                    record = repo.session.get(SyncReconciliationRun, run_id)
                    if record.status == 'RUNNING':
                        record.source_count = source['count']
                        incomplete = budget_exhausted or self.cancelled.is_set() or counts['NOT_VERIFIED'] or metadata_failed or source.get('error_code')
                        ratio = record.mismatched_count / max(1, record.source_count, record.target_count)
                        record.status = 'BLOCKED' if ratio > self.engine.max_ratio else 'PARTIAL' if incomplete else 'COMPLETED'
                        record.finished_at = now()
                        record.summary_meta = {**record.summary_meta, 'mismatch_ratio': ratio,
                            'max_allowed_ratio': self.engine.max_ratio,
                            **({'reason': 'RECONCILIATION_BUDGET'} if budget_exhausted else {}),
                            **({'reason': 'RECONCILIATION_INTERRUPTED'} if self.cancelled.is_set() else {})}
        except Exception:
            state['error'] = 'RECONCILIATION_FAILED'
            if run_id:
                with self.engine.uow() as repo:
                    record = repo.session.get(SyncReconciliationRun, run_id)
                    if record and record.status == 'RUNNING':
                        record.status, record.finished_at = 'FAILED', now()
                        record.summary_meta = {**(record.summary_meta or {}), 'reason': 'RECONCILIATION_FAILED'}
        finally:
            ready.set()
            self.engine.file_cancelled = None
            self.engine.file_read_budget_seconds, self.engine.file_max_bytes = previous_seconds, previous_bytes
            self.engine._run_lock.release()

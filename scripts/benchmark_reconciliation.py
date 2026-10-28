"""Reproducible local SQLite/files benchmark; never loads dotenv or production URLs."""
import argparse
import hashlib
import json
import subprocess
import tempfile
import time
import tracemalloc
import types
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event, text

from data_sync_etl.adapters.persistence.reconciliation_engine import ReconciliationEngine
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.config import Settings
from data_sync_etl.db.models import (
    Base,
    ReconciliationAuditJob,
    ResourceVersion,
    SyncReconciliationRun,
)
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.core import new_id, now

BASELINE = '7e9099217f49536e2cbde629e90257bc52df8cf6'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--files', type=int, default=1000)
    args = parser.parse_args()
    baseline = types.ModuleType('baseline_reconciliation')
    source = subprocess.run(['git', 'show', BASELINE + ':src/data_sync_etl/adapters/persistence/reconciliation_engine.py'],
                            capture_output=True, check=True, encoding='utf-8').stdout
    exec(compile(source, '<baseline>', 'exec'), baseline.__dict__)
    domain_source = subprocess.run(['git', 'show', BASELINE + ':src/data_sync_etl/domain/reconciliation.py'],
        capture_output=True, check=True, encoding='utf-8').stdout
    baseline_domain = types.ModuleType('baseline_reconciliation_domain')
    exec(compile(domain_source, '<baseline-domain>', 'exec'), baseline_domain.__dict__)
    for name in ('METADATA_FIELDS', 'metadata_differences', 'serialize_json_value', 'STREAM_TABLE_MAPPING'):
        setattr(baseline, name, getattr(baseline_domain, name))
    results = {'baseline_commit': BASELINE, 'environment': 'SQLite + synthetic LOCAL byte fixtures',
               'file_count': args.files, 'results': []}
    with tempfile.TemporaryDirectory(prefix='audit-benchmark-', dir='.test-tmp') as directory:
        root = Path(directory)
        engine = make_engine('sqlite:///' + str(root / 'master.db'))
        Base.metadata.create_all(engine)
        uow = SQLAlchemyUnitOfWork(sessions(engine))
        settings = Settings(_env_file=None, reconciliation_max_files_per_run=10000,
                            reconciliation_audit_batch_size=100)
        current = ReconciliationEngine(uow, storage_root=root)
        old = baseline.ReconciliationEngine(uow, storage_root=root)
        with uow() as repo:
            resource = repo.insert('master_learning_resources', {'id': 'benchmark', 'source_origin': 'LOCAL',
                'resource_type_id': 'type', 'resource_type_code': 'DOC', 'resource_type_name_vi': 'Fixture',
                'resource_type_is_active': True, 'publication_status': 'PUBLISHED', 'is_active': True})
            for i in range(args.files):
                suffix = ['.pdf', '.docx', '.mp3', '.mp4'][i % 4]
                blob = bytes([i % 251]) * (1024 * (i % 4 + 1))
                name = f'file-{i:05}{suffix}'
                (root / name).write_bytes(blob)
                repo.session.add(ResourceVersion(id=f'v{i:05}', resource_id=resource['id'],
                    version_number=i + 1, lifecycle_status='APPROVED', storage_bucket='',
                    storage_object_key=name, source_hash=hashlib.sha256(blob).hexdigest(), source_file_size=len(blob)))
        audit = ScheduledAudit(current, settings)

        def measure(name, fn):
            tracemalloc.start()
            started = time.perf_counter()
            value = fn()
            elapsed = (time.perf_counter() - started) * 1000
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            results['results'].append({'scenario': name, 'execution_ms': round(elapsed, 3),
                                      'python_peak_bytes': peak, **value})

        def legacy():
            counters = {'bytes_read': 0, 'sha256_computed_count': 0}
            original = Path.open
            class Reader:
                def __init__(self, file): self.file = file
                def __enter__(self):
                    self.file.__enter__()
                    counters['sha256_computed_count'] += 1
                    return self
                def __exit__(self, *a): return self.file.__exit__(*a)
                def read(self, n=-1):
                    block = self.file.read(n)
                    counters['bytes_read'] += len(block)
                    return block
            def opened(path, mode='r', *a, **kw):
                file = original(path, mode, *a, **kw)
                return Reader(file) if mode == 'rb' and path.parent == root else file
            with patch.object(Path, 'open', opened), uow() as repo:
                rows = old.verify_file_blobs(repo.session)
            return {**counters, 'discrepancies': sum(r['discrepancy_flag'] != 'SYNCED' for r in rows)}

        def light():
            with uow() as repo:
                rows = current.verify_file_blobs(repo.session, audit_type='LIGHT')
            return {**current.file_metrics, 'not_verified_count': sum(r['discrepancy_flag'] == 'NOT_VERIFIED' for r in rows)}

        def scheduled():
            job_id = audit.queue.enqueue()
            audit.once()
            with uow() as repo:
                job = repo.session.get(ReconciliationAuditJob, job_id)
                return {'status': job.status, **job.checkpoint}

        measure('before_cold_deep', legacy)
        measure('after_light', light)
        measure('after_first_due_deep', scheduled)
        measure('after_unchanged_due_selection', scheduled)
        corrupted = root / 'file-00000.pdf'
        stamp = corrupted.stat()
        corrupted.write_bytes(b'!' * stamp.st_size)
        import os
        os.utime(corrupted, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        measure('before_cached_same_size_corruption', legacy)
        def fresh():
            with uow() as repo:
                rows = current.verify_file_blobs(repo.session, version_ids=['v00000'])
            return {**current.file_metrics, 'flag': rows[0]['discrepancy_flag']}
        measure('after_explicit_deep_same_size_corruption', fresh)
        source_engine = make_engine('sqlite:///' + str(root / 'source.db'))
        old_stamp = now() - timedelta(days=1)
        with source_engine.begin() as conn:
            conn.execute(text('CREATE TABLE subjects (id text primary key, updated_at timestamp, subject_name text, subject_slug text, grade_id text, is_active boolean, deleted_at timestamp)'))
            conn.execute(text("INSERT INTO subjects VALUES (:id,:stamp,'same','same',NULL,1,NULL)"),
                [{'id': f's{i:04}', 'stamp': old_stamp.isoformat()} for i in range(1000)])
        with uow() as repo:
            for i in range(1000):
                repo.insert('subjects', {'id': f's{i:04}', 'subject_name': 'same', 'subject_slug': 'same',
                    'is_active': True, 'source_origin': 'POSTGRESQL', 'updated_at': old_stamp})
            repo.session.add(SyncReconciliationRun(run_id=new_id(), stream_name='subjects',
                status='COMPLETED', started_at=now() - timedelta(hours=1)))
        old.source_adapter = current.source_adapter = SimpleNamespace(engine=source_engine)
        def metadata(which):
            counts = {'source_select_round_trips': 0, 'master_select_round_trips': 0}
            def source_query(conn, cursor, statement, parameters, context, many):
                if statement.lstrip().upper().startswith('SELECT'):
                    counts['source_select_round_trips'] += 1
            def master_query(conn, cursor, statement, parameters, context, many):
                if statement.lstrip().upper().startswith('SELECT'):
                    counts['master_select_round_trips'] += 1
            event.listen(source_engine, 'before_cursor_execute', source_query)
            event.listen(engine, 'before_cursor_execute', master_query)
            try:
                report = which.execute_stream_reconcile('subjects')['reports'][0]
                return {**counts, 'mismatched_count': report['mismatched_count'],
                    'source_count_reported': report['source_count'], 'target_count_reported': report['target_count'],
                    'sample_flags': [r['discrepancy_flag'] for r in report['discrepancies_sample']]}
            finally:
                event.remove(source_engine, 'before_cursor_execute', source_query)
                event.remove(engine, 'before_cursor_execute', master_query)
        measure('before_1000_unchanged_metadata', lambda: metadata(old))
        measure('after_1000_unchanged_metadata', lambda: metadata(current))
        with source_engine.begin() as conn:
            conn.execute(text("UPDATE subjects SET subject_name='changed',updated_at=:stamp WHERE id='s0000'"),
                {'stamp': now().isoformat()})
        measure('before_one_changed_metadata', lambda: metadata(old))
        measure('after_one_changed_metadata', lambda: metadata(current))
        source_engine.dispose()
        engine.dispose()
    results['not_measured'] = ['real PostgreSQL round trips/rows scanned', 'SOURCE provider benchmark',
        'OS RSS peak memory', 'real media decoding (fixtures exercise integrity bytes only)']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps(results, indent=2, default=str))


if __name__ == '__main__':
    main()

"""Explicit bounded tombstones: complete snapshot, provenance, preview fingerprint."""

import hashlib
import json

from data_sync_etl.application.sync import STREAMS
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.source_ownership import state_hash
from data_sync_etl.ports.source import ReconciliationSource


class ReconcileMaster:
    def __init__(self, source: ReconciliationSource, uow, *, batch_size=100, max_affected=100, max_ratio=0.1):
        if not 1 <= batch_size <= 1000 or not 1 <= max_affected <= 1000 or not 0 < max_ratio <= 1:
            raise ValueError('Invalid reconciliation safety bounds')
        self.source, self.uow = source, uow
        self.batch_size, self.max_affected, self.max_ratio = batch_size, max_affected, max_ratio

    def run(self, stream='all', *, apply=False, fingerprint=None):
        if stream != 'all' and stream not in STREAMS:
            raise DomainError('Unknown reconciliation stream')
        if getattr(self.source, 'source_origin', None) != 'POSTGRESQL':
            return {'status': 'BLOCKED', 'error_code': 'BLOCKED_SOURCE_ADAPTER'}
        try:
            with self.uow.sync_lock() as acquired:
                if not acquired:
                    raise DomainError('BLOCKED_SYNC_BUSY')
                plan, candidates = self._plan(stream)
                if not apply or plan['status'] == 'BLOCKED':
                    return plan
                if not fingerprint or fingerprint != plan['fingerprint']:
                    raise DomainError('BLOCKED_PLAN_CHANGED')
                # Bound the complete write-set, verify every row/absence BEFORE any
                # update, then commit atomically. No partial apply on query failure.
                with self.uow() as repo:
                    locked = []
                    for table, record_id, expected in candidates:
                        row = repo.get(table, record_id, lock=True)
                        if row is None or state_hash(row) != expected or row['source_origin'] != 'POSTGRESQL':
                            raise DomainError('BLOCKED_MASTER_CHANGED')
                        if row['source_state_hash'] != state_hash(row):
                            raise DomainError('BLOCKED_LOCAL_SOURCE_CHANGE')
                        locked.append((table, row))
                    # Fresh read-only snapshot after locking target rows. Source is
                    # never locked/written; a later cross-database race remains an
                    # operational limitation (no distributed transaction/CDC).
                    with self.source.snapshot() as reader:
                        for name in (STREAMS if stream == 'all' else [stream]):
                            summary = reader.reconciliation_summary(name)
                            if summary != plan['streams'][name]['source_summary']:
                                raise DomainError('BLOCKED_SOURCE_CHANGED')
                            source_digest, after = hashlib.sha256(), None
                            while True:
                                page = reader.reconciliation_ids(name, after, self.batch_size)
                                if not page:
                                    break
                                for record_id in page:
                                    source_digest.update(json.dumps(record_id).encode())
                                after = page[-1]
                            if source_digest.hexdigest() != plan['streams'][name]['source_fingerprint']:
                                raise DomainError('BLOCKED_SOURCE_CHANGED')
                            ids = [row['id'] for table, row in locked if table == STREAMS[name]]
                            if ids and reader.reconciliation_present(name, ids):
                                raise DomainError('BLOCKED_SOURCE_REAPPEARED')
                    stamp = now()
                    for table, row in locked:
                        active, deleted = self._state_fields(table)
                        values = {active: False, 'source_missing_at': stamp}
                        if row[deleted] is None:
                            values[deleted] = stamp
                        values['source_state_hash'] = state_hash({**row, **values})
                        repo.update(table, row['id'], values)
                return {**plan, 'status': 'RECONCILIATION_APPLIED', 'confirmed': len(candidates), 'detected_at': stamp.isoformat()}
        except Exception as exc:
            safe = {'BLOCKED_SYNC_BUSY', 'BLOCKED_PLAN_CHANGED', 'BLOCKED_MASTER_CHANGED', 'BLOCKED_LOCAL_SOURCE_CHANGE',
                    'BLOCKED_SOURCE_CHANGED', 'BLOCKED_SOURCE_REAPPEARED', 'BLOCKED_DEPENDENCY', 'BLOCKED_SOURCE_COVERAGE'}
            code = str(exc) if isinstance(exc, DomainError) and str(exc) in safe else 'BLOCKED_SNAPSHOT_OR_SCHEMA'
            return {'status': 'BLOCKED', 'error_code': code}

    @staticmethod
    def _state_fields(table):
        return ('grade_is_active', 'grade_deleted_at') if table == 'master_grade_levels' else ('is_active', 'deleted_at')

    def _plan(self, stream):
        digest, reports, candidates = hashlib.sha256(), {}, []
        blocked = None
        with self.source.snapshot() as reader:
            for name in (STREAMS if stream == 'all' else [stream]):
                table = STREAMS[name]
                summary = reader.reconciliation_summary(name)
                digest.update(json.dumps([name, summary, self.max_affected, self.max_ratio], sort_keys=True).encode())
                after, read, pages, source_digest = None, 0, 0, hashlib.sha256()
                while True:
                    ids = reader.reconciliation_ids(name, after, self.batch_size)
                    if not ids:
                        break
                    for record_id in ids:
                        if not isinstance(record_id, str) or not record_id.strip() or len(record_id) > 30 or (after is not None and record_id <= after):
                            raise DomainError('BLOCKED_SOURCE_COVERAGE')
                        digest.update(json.dumps(record_id).encode())
                        source_digest.update(json.dumps(record_id).encode())
                        after = record_id
                    read += len(ids)
                    pages += 1
                if read != summary['count']:
                    raise DomainError('BLOCKED_SOURCE_COVERAGE')
                report = dict(source_count=read, source_summary=summary, source_pages=pages, source_fingerprint=source_digest.hexdigest(),
                              eligible_master_count=0, local_excluded=0, unknown_excluded=0,
                              missing_candidate_count=0, already_inactive_count=0, sample_ids=[])
                after = None
                while True:
                    with self.uow() as repo:
                        rows = repo.reconciliation_page(table, after, self.batch_size)
                    if not rows:
                        break
                    present = reader.reconciliation_present(name, [row['id'] for row in rows])
                    for row in rows:
                        digest.update(json.dumps([table, row['id'], row['source_origin'], row['source_state_hash'], row['source_missing_at'], state_hash(row)], default=str).encode())
                        if row['source_origin'] != 'POSTGRESQL':
                            report['local_excluded' if row['source_origin'] == 'LOCAL' else 'unknown_excluded'] += 1
                            continue
                        report['eligible_master_count'] += 1
                        if row['id'] in present:
                            continue
                        active, deleted = self._state_fields(table)
                        if not row[active] or row[deleted] is not None:
                            report['already_inactive_count'] += 1
                            continue
                        report['missing_candidate_count'] += 1
                        if len(report['sample_ids']) < 20:
                            report['sample_ids'].append(row['id'])
                        if len(candidates) < self.max_affected:
                            candidates.append((table, row['id'], state_hash(row)))
                        else:
                            blocked = 'BLOCKED_SOURCE_EMPTY_OR_MASS_CHANGE'
                        if not row['source_state_hash'] or row['source_state_hash'] != state_hash(row):
                            blocked = 'BLOCKED_LOCAL_SOURCE_CHANGE'
                    after = rows[-1]['id']
                if not read or report['missing_candidate_count'] / max(1, report['eligible_master_count']) > self.max_ratio:
                    blocked = 'BLOCKED_SOURCE_EMPTY_OR_MASS_CHANGE'
                reports[name] = report
        return dict(status='BLOCKED' if blocked else 'RECONCILIATION_PREVIEW', error_code=blocked,
                    fingerprint=digest.hexdigest(), streams=reports, snapshot_complete=True, previewed_at=now().isoformat()), candidates

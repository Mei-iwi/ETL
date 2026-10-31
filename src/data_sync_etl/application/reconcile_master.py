"""Explicit bounded tombstones: complete snapshot, provenance, preview fingerprint."""

import hashlib
import json

from data_sync_etl.application.sync import STREAMS
from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.source_ownership import state_hash
from data_sync_etl.ports.source import ReconciliationSource


class ReconcileMaster:
    def __init__(self, source: ReconciliationSource, uow, *, batch_size=100, max_affected=100,
                 max_ratio=0.1, source_registry=None, federation_enabled=False):
        if not 1 <= batch_size <= 1000 or not 1 <= max_affected <= 1000 or not 0 < max_ratio <= 1:
            raise ValueError('Invalid reconciliation safety bounds')
        self.source, self.uow = source, uow
        self.batch_size, self.max_affected, self.max_ratio = batch_size, max_affected, max_ratio
        self.source_registry, self.federation_enabled = source_registry, federation_enabled

    def run(self, stream='all', *, apply=False, fingerprint=None, source_id=None):
        if stream != 'all' and stream not in STREAMS:
            raise DomainError('Unknown reconciliation stream')
        if self.federation_enabled:
            if not source_id or self.source_registry is None:
                return {'status': 'BLOCKED', 'error_code': 'SOURCE_ID_REQUIRED'}
            return self._run_federated(stream, source_id, apply=apply, fingerprint=fingerprint)
        if source_id is not None:
            return {'status': 'BLOCKED', 'error_code': 'SOURCE_FEDERATION_DISABLED'}
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

    def _federated_plan(self, source_id, stream):
        source = self.source_registry.adapter_for_source_id(source_id, 'read_metadata')
        probe = source.locator('resources', '__registry_probe__')
        candidates, reports, fingerprint = [], {}, hashlib.sha256()
        names = (STREAMS if stream == 'all' else [stream])
        for name in names:
            source_count = source_pages = missing_count = 0
            source_digest = hashlib.sha256()
            missing_samples = []
            source_after = master_after = None
            source_batch, master_batch = [], []
            source_index = master_index = 0
            with source.snapshot() as reader:
                summary = reader.reconciliation_summary(name)
                with self.uow() as repo:
                    target_count = repo.count_federated_mappings(
                        source_id, name, source_backend=probe.backend, source_namespace=probe.namespace)
                    local_excluded = repo.count(STREAMS[name], {'source_origin': 'LOCAL'})

                def next_source_key(_name=name, _source_digest=source_digest):
                    nonlocal source_batch, source_index, source_after, source_count, source_pages
                    while source_index >= len(source_batch):
                        source_batch = reader.reconciliation_ids(_name, source_after, self.batch_size)
                        source_index = 0
                        if not source_batch:
                            return None
                        if source_batch != sorted(set(source_batch)) or (
                                source_after is not None and source_batch[0] <= source_after):
                            raise DomainError('BLOCKED_SOURCE_COVERAGE')
                        for source_key in source_batch:
                            if not isinstance(source_key, str) or not source_key.strip() or len(source_key) > 255:
                                raise DomainError('BLOCKED_SOURCE_COVERAGE')
                            _source_digest.update(json.dumps(source_key).encode())
                        source_count += len(source_batch)
                        source_pages += 1
                        source_after = source_batch[-1]
                    source_key = source_batch[source_index]
                    source_index += 1
                    return source_key

                def next_master_mapping(_name=name):
                    nonlocal master_batch, master_index, master_after
                    while master_index >= len(master_batch):
                        with self.uow() as repo:
                            master_batch = repo.federated_mapping_page(
                                source_id, _name, source_backend=probe.backend,
                                source_namespace=probe.namespace, after=master_after,
                                size=self.batch_size)
                        master_index = 0
                        if not master_batch:
                            return None
                        master_after = master_batch[-1]['source_record_key']
                        for mapping in master_batch:
                            fingerprint.update(json.dumps([
                                source_id, _name, mapping['source_record_key'], mapping['master_id'],
                                mapping['source_identity_hash']], sort_keys=True).encode())
                    mapping = master_batch[master_index]
                    master_index += 1
                    return mapping

                source_key, mapping = next_source_key(), next_master_mapping()
                while source_key is not None or mapping is not None:
                    if mapping is None or (source_key is not None and
                                           mapping['source_record_key'] > source_key):
                        source_key = next_source_key()
                    elif source_key is None or mapping['source_record_key'] < source_key:
                        missing_count += 1
                        if len(missing_samples) < 20:
                            missing_samples.append(mapping['master_id'])
                        if len(candidates) < self.max_affected:
                            candidates.append((name, mapping['source_record_key'], mapping['master_id']))
                        mapping = next_master_mapping()
                    else:
                        source_key, mapping = next_source_key(), next_master_mapping()
                if source_count != summary.get('count'):
                    raise DomainError('BLOCKED_SOURCE_COVERAGE')
            fingerprint.update(json.dumps([summary, source_digest.hexdigest(), self.max_affected,
                                           self.max_ratio], sort_keys=True).encode())
            reports[name] = {
                'source_count': source_count, 'source_summary': summary, 'source_pages': source_pages,
                'source_fingerprint': source_digest.hexdigest(), 'eligible_master_count': target_count,
                'local_excluded': local_excluded, 'missing_candidate_count': missing_count,
                'sample_master_ids': missing_samples,
            }
            if (source_count == 0 or missing_count > self.max_affected
                    or missing_count / max(1, target_count) > self.max_ratio):
                reports[name]['status'] = 'BLOCKED_MASS_CHANGE'
        blocked = any(report.get('status') == 'BLOCKED_MASS_CHANGE' for report in reports.values())
        return {'status': 'BLOCKED' if blocked else 'RECONCILIATION_PREVIEW',
                'error_code': 'BLOCKED_SOURCE_EMPTY_OR_MASS_CHANGE' if blocked else None,
                'source_id': source_id, 'fingerprint': fingerprint.hexdigest(), 'streams': reports,
                'candidate_count': len(candidates), 'sample_master_ids': [row[2] for row in candidates[:20]],
                'snapshot_complete': True, 'previewed_at': now().isoformat()}, candidates

    def _run_federated(self, stream, source_id, *, apply, fingerprint):
        try:
            source = self.source_registry.adapter_for_source_id(source_id, 'read_metadata')
            probe = source.locator('resources', '__registry_probe__')
            plan, candidates = self._federated_plan(source_id, stream)
            if not apply or plan['status'] == 'BLOCKED':
                plan['run_id'] = self._save_federated_audit(plan, candidates, applied=False)
                return plan
            if not fingerprint or fingerprint != plan['fingerprint']:
                raise DomainError('BLOCKED_PLAN_CHANGED')
            stamp = now()
            with self.uow.sync_lock() as acquired:
                if not acquired:
                    raise DomainError('BLOCKED_SYNC_BUSY')
                fresh, fresh_candidates = self._federated_plan(source_id, stream)
                if fresh['fingerprint'] != fingerprint or fresh_candidates != candidates:
                    raise DomainError('BLOCKED_SOURCE_CHANGED')
                with self.uow() as repo:
                    for name, source_key, _ in candidates:
                        repo.mark_federated_source_missing(source_id, name, source_key, stamp,
                                                           source_backend=probe.backend,
                                                           source_namespace=probe.namespace)
                    plan = {**plan, 'status': 'FEDERATED_RECONCILIATION_APPLIED',
                            'confirmed': len(candidates), 'detected_at': stamp.isoformat()}
                    plan['run_id'] = self._save_federated_audit(plan, candidates, applied=True, repo=repo)
            return plan
        except Exception as exc:
            safe = {'BLOCKED_PLAN_CHANGED', 'BLOCKED_SOURCE_CHANGED', 'BLOCKED_SYNC_BUSY',
                    'BLOCKED_SOURCE_COVERAGE', 'BLOCKED_LOCAL_SOURCE_CHANGE', 'BLOCKED_SOURCE_MAPPING'}
            code = str(exc) if isinstance(exc, DomainError) and str(exc) in safe else 'BLOCKED_FEDERATED_RECONCILIATION'
            return {'status': 'BLOCKED', 'source_id': source_id, 'error_code': code}

    def _save_federated_audit(self, plan, candidates, *, applied, repo=None):
        def save(target):
            run_id = new_id()
            source_key = hashlib.sha256(plan['source_id'].encode()).hexdigest()[:12]
            scope = f"F{source_key}:all"
            target.insert('sync_reconciliation_runs', {
                'run_id': run_id, 'stream_name': scope[:50], 'started_at': now(), 'finished_at': now(),
                'status': plan['status'],
                'source_count': sum(r['source_count'] for r in plan['streams'].values()),
                'target_count': sum(r['eligible_master_count'] for r in plan['streams'].values()),
                'matched_count': 0, 'mismatched_count': len(candidates),
                'summary_meta': {**plan, 'applied': applied},
            })
            for stream_name, source_key, master_id in candidates:
                target.insert('sync_reconciliation_discrepancies', {
                    'id': new_id(), 'run_id': run_id, 'record_id': master_id,
                    'stream_name': f"F{hashlib.sha256(plan['source_id'].encode()).hexdigest()[:12]}:{stream_name}"[:50],
                    'discrepancy_flag': 'ORPHAN_MASTER',
                    'source_value': {'source_record_key': source_key},
                    'target_value': {'master_id': master_id}, 'detected_at': now(),
                    'resolution_status': 'SYNCED' if applied else 'UNRESOLVED',
                })
            return run_id
        if repo is not None:
            return save(repo)
        else:
            with self.uow() as target:
                return save(target)

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

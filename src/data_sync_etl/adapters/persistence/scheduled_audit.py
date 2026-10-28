"""Budgeted audits; exact-ID metadata repair requires an explicit admin request."""
from copy import copy
from datetime import datetime, timedelta
from hashlib import sha256
from threading import Event, Thread
from time import monotonic
from types import SimpleNamespace

from sqlalchemy import func, select, text

from data_sync_etl.adapters.persistence.audit_metadata import AUTH_FIELDS, metadata_pages
from data_sync_etl.adapters.persistence.audit_queue import AuditQueue, utc
from data_sync_etl.db.models import (
    MODELS,
    MasterLearningResource,
    ResourceVersion,
)
from data_sync_etl.db.models import (
    ReconciliationAuditJob as Job,
)
from data_sync_etl.db.models import (
    ReconciliationFileState as FileState,
)
from data_sync_etl.db.models import (
    SyncReconciliationDiscrepancy as Discrepancy,
)
from data_sync_etl.db.models import (
    SyncReconciliationRun as Run,
)
from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.reconciliation import (
    METADATA_FIELDS,
    STREAM_TABLE_MAPPING,
    metadata_differences,
    observe_file_result,
    serialize_json_value,
)
from data_sync_etl.workers.lifecycle import tick


def identity(version, origin):
    return sha256(str((version.source_hash, version.source_file_size, version.storage_bucket,
        version.storage_object_key, version.version_number, version.parent_version_id, origin)).encode()).hexdigest()


FILE_COLUMNS = ('id', 'resource_id', 'source_hash', 'source_file_size', 'storage_file_size',
                'storage_bucket', 'storage_object_key', 'version_number', 'parent_version_id')


class ScheduledAudit:
    def __init__(self, engine, settings, sync=None):
        self.engine, self.settings = engine, settings
        self.sync = sync
        self.queue = AuditQueue(engine.uow, settings)

    def schedule(self):
        if not self.settings.reconciliation_scheduler_enabled:
            return []  # No DB read or SOURCE activity while disabled.
        from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
        return AuditSchedule(self.engine.uow, self.settings).tick()

    def once(self, job_id=None):
        claim = self.queue.claim(job_id)
        if claim is None:
            return False
        stop, lost = Event(), Event()

        def heartbeat():
            while not stop.wait(self.settings.reconciliation_lease_seconds / 3):
                try:
                    self.queue.heartbeat(claim)
                    tick()
                except Exception:
                    lost.set()
                    return

        thread = Thread(target=heartbeat, daemon=True, name='audit-heartbeat')
        thread.start()
        try:
            with self.engine.uow.processing_lock(f"reconciliation-{claim['stream_name']}") as acquired:
                if not acquired:
                    raise DomainError('RECONCILIATION_BUSY')
                if claim['audit_type'] in ('LIGHT', 'DEEP'):
                    self._files(claim, lost)
                elif claim['audit_type'] == 'IDS':
                    self._ids(claim, lost)
                else:
                    self._metadata(claim)
        except Exception:
            self.queue.fail(claim)
        finally:
            stop.set()
            thread.join(timeout=2)
        return True

    def _run(self, session, claim):
        run = session.get(Run, claim['id'])
        if run is None:
            run = Run(run_id=claim['id'], stream_name=claim['stream_name'], status='RUNNING',
                started_at=now(), source_count=0, target_count=0, matched_count=0, mismatched_count=0,
                summary_meta={})
            session.add(run)
        return run

    def _save(self, claim, checkpoint, results=(), file_state=None, status=None):
        checkpoint.setdefault('queue_wait_seconds', claim.get('queue_wait_seconds', 0))
        with self.engine.uow() as repo:
            job = self.queue.owned(repo.session, claim)
            run = self._run(repo.session, claim)
            run.status, run.finished_at = 'RUNNING', None
            job.checkpoint = serialize_json_value(checkpoint)
            run.summary_meta = {**serialize_json_value(checkpoint), 'audit_type': claim['audit_type'],
                'dry_run': not checkpoint.get('repair_requested', False),
                'mode': checkpoint.get('mode', 'INCREMENTAL'), 'whole_repository_verified': False,
                'verification_scope': 'AUDIT_JOB', 'cache_reused_count': 0}
            run.source_count = checkpoint.get('source_count', 0)
            run.target_count = checkpoint.get('records_scanned', 0)
            run.matched_count = checkpoint.get('matched_count', 0)
            run.mismatched_count = checkpoint.get('mismatched_count', 0)
            if file_state:
                version_id, values = file_state
                current = repo.session.get(ResourceVersion, version_id)
                resource = repo.session.get(MasterLearningResource, current.resource_id) if current else None
                if (not current or not resource or values.get('observed_identity') is not None
                        and identity(current, resource.source_origin) != values['observed_identity']):
                    raise DomainError('AUDIT_VERSION_CHANGED')
                state = repo.session.get(FileState, version_id)
                if state is None:
                    state = FileState(resource_version_id=version_id, status='NOT_VERIFIED')
                    repo.session.add(state)
                for key, value in values.items():
                    setattr(state, key, value)
            repo.session.flush()
            for result in results:
                # A result and its checkpoint commit atomically; a crash cannot duplicate it.
                repo.session.add(Discrepancy(id=new_id(), run_id=run.run_id,
                    stream_name=claim['stream_name'], record_id=result['record_id'],
                    discrepancy_flag=result['discrepancy_flag'], source_value=serialize_json_value(result.get('source_value')),
                    target_value=serialize_json_value(result.get('target_value')), detected_at=now(), resolution_status='UNRESOLVED'))
            if status:
                job.status, job.finished_at = status, now()
                job.claim_token, job.lease_expires_at = None, None
                run.status, run.finished_at = status, now()

    def _files(self, claim, lost):
        engine = copy(self.engine)
        engine.file_cancelled = lost
        settings = self.settings
        started = monotonic()
        checkpoint = dict(claim['checkpoint'])
        # Per-job metrics are cumulative through crash recovery.
        for key in ('records_scanned', 'bytes_read', 'sha256_computed_count', 'matched_count', 'mismatched_count', 'not_verified_count'):
            checkpoint.setdefault(key, 0)
        used_bytes, visited = checkpoint['bytes_read'], checkpoint['records_scanned']
        after = checkpoint.get('last_id')
        self._save(claim, checkpoint)
        exhausted = False
        while visited < settings.reconciliation_max_files_per_run and monotonic() - started < settings.reconciliation_max_seconds_per_run:
            if lost.is_set():
                raise DomainError('AUDIT_LEASE_LOST')
            # Only a bounded page of detached metadata. No open DB transaction while hashing.
            with engine.uow() as repo:
                query = select(*[getattr(ResourceVersion, key) for key in FILE_COLUMNS],
                    MasterLearningResource.source_origin, FileState).join_from(ResourceVersion,
                    MasterLearningResource, MasterLearningResource.id == ResourceVersion.resource_id).outerjoin(
                    FileState, FileState.resource_version_id == ResourceVersion.id)
                if after:
                    query = query.where(ResourceVersion.id > after)
                rows = repo.session.execute(query.order_by(ResourceVersion.id).limit(settings.reconciliation_audit_batch_size)).all()
                page = [(SimpleNamespace(**dict(zip(FILE_COLUMNS, row[:len(FILE_COLUMNS)], strict=True))), row[-2],
                         {c.name: getattr(row[-1], c.name) for c in FileState.__table__.columns} if row[-1] else None)
                        for row in rows]
            if not page:
                exhausted = True
                break
            for version, origin, previous in page:
                if visited >= settings.reconciliation_max_files_per_run or monotonic() - started >= settings.reconciliation_max_seconds_per_run:
                    break
                observed = identity(version, origin)
                due = (claim['audit_type'] == 'LIGHT' or not previous or previous['observed_identity'] != observed
                       or previous['next_due_at'] is None or utc(previous['next_due_at']) <= now())
                if not due:
                    after = version.id
                    checkpoint['last_id'] = after
                    continue  # Explicitly skipped, never credited as freshly verified.
                engine.file_max_bytes = settings.reconciliation_max_bytes_per_run - used_bytes
                if engine.file_max_bytes <= 0:
                    break
                engine.file_read_budget_seconds = min(settings.reconciliation_file_timeout_seconds,
                    settings.reconciliation_max_seconds_per_run - (monotonic() - started))
                result = engine.verify_file_snapshot([version], {version.resource_id: origin}, audit_type=claim['audit_type'])[0]
                observe_file_result(checkpoint, result)
                metrics = engine.file_metrics
                used_bytes += metrics['bytes_read']
                visited += 1
                checkpoint['records_scanned'] += 1
                for key in ('bytes_read', 'sha256_computed_count'):
                    checkpoint[key] += metrics[key]
                flag = result['discrepancy_flag']
                checkpoint['matched_count' if flag == 'SYNCED' else 'not_verified_count' if flag == 'NOT_VERIFIED' else 'mismatched_count'] += 1
                values = result.get('target_value') or {}
                reason = values.get('reason_code')
                # Never export SOURCE locators or local/private paths through the audit API.
                if flag != 'SYNCED':
                    result = {'record_id': version.id, 'discrepancy_flag': flag, 'source_value': None,
                              'target_value': {k: v for k, v in values.items() if k in ('reason_code', 'size_status', 'verification', 'storage_file_size')}}
                attempts = (previous or {}).get('attempts', 0) + 1
                state = {'last_attempt_at': now(), 'status': flag, 'reason_code': reason,
                         'observed_identity': observed}
                if values.get('observed_revision'):
                    state['last_observed_revision'] = values['observed_revision']
                    if previous and previous['last_observed_revision'] != values['observed_revision']:
                        state['next_due_at'] = now()
                if claim['audit_type'] == 'DEEP':
                    state.update(observed_identity=observed, attempts=0 if flag == 'SYNCED' else attempts,
                        next_due_at=now() + timedelta(days=settings.reconciliation_deep_coverage_days)
                        if flag == 'SYNCED' or attempts >= settings.reconciliation_retry_max_attempts
                        else now() + timedelta(seconds=min(3600, settings.reconciliation_retry_seconds * 2 ** min(attempts - 1, 10))))
                    if flag == 'SYNCED':
                        state.update(last_verified_at=now(), last_verified_hash=version.source_hash,
                            last_verified_size=values.get('verified_size', values.get('storage_file_size')),
                            verification_method='SHA256')
                elif flag not in ('NOT_VERIFIED', 'SYNCED') or previous and previous['observed_identity'] != observed:
                    state['next_due_at'] = now()
                if (claim['audit_type'] == 'LIGHT' and previous and flag == 'NOT_VERIFIED'
                        and previous['status'] in ('DESYNC_CHECKSUM', 'DESYNC_BLOB_SIZE',
                                                   'DESYNC_METADATA', 'MUTATED_SOURCE')):
                    # A size match cannot clear an unresolved deep discrepancy.
                    state.update(status=previous['status'],
                        reason_code=previous['reason_code'] or 'DEEP_DISCREPANCY_UNRESOLVED')
                after = version.id
                checkpoint.update(last_id=after, execution_time_ms=round((monotonic() - started) * 1000, 2),
                    queue_wait_seconds=claim.get('queue_wait_seconds', 0))
                self._save(claim, checkpoint, [result] if flag != 'SYNCED' else [], (version.id, state))
            if used_bytes >= settings.reconciliation_max_bytes_per_run:
                break
        if exhausted:
            checkpoint.update(last_id=None, generation=checkpoint.get('generation', 0) + 1)
        checkpoint.update(coverage_status='CYCLE_VISITED' if exhausted else 'COVERAGE_BEHIND',
            cycle_finished=exhausted, io_concurrency=1)
        # A visited cycle is not a claim that all files have current valid SHA-256.
        self._save(claim, checkpoint, status='PARTIAL' if not exhausted or checkpoint['not_verified_count'] else 'COMPLETED')

    def _metadata(self, claim):
        checkpoint = dict(claim['checkpoint'])
        stream = claim['stream_name']
        checkpoint.setdefault('auth_scope_complete', True)
        self._repair_pending(claim, checkpoint)
        if 'scan_started' not in checkpoint:
            with self.engine.uow() as repo:
                query = select(Job).where(Job.audit_type == 'METADATA',
                    Job.stream_name == stream, Job.status == 'COMPLETED')
                if checkpoint.get('repair_requested'):
                    query = query.where(Job.checkpoint['repair_requested'].as_boolean().is_(True))
                previous = repo.session.scalar(query.order_by(Job.created_at.desc()).limit(1))
                since = ((previous.checkpoint or {}).get('watermark_next') if previous else None)
                if checkpoint.get('mode') == 'FULL':
                    since = None
            checkpoint.update(scan_started=now().isoformat(), since=since, last_id=None)
        since = datetime.fromisoformat(checkpoint['since']) - timedelta(seconds=300) if checkpoint.get('since') else None
        for key in ('records_scanned', 'matched_count', 'mismatched_count', 'not_verified_count', 'source_pages_read'):
            checkpoint.setdefault(key, 0)
        self._save(claim, checkpoint)
        started = monotonic()
        visited, complete = 0, True
        model = MODELS[STREAM_TABLE_MAPPING[stream]['target_table']]
        # SQL SOURCE uses ID keysets. Immutable fixtures use timestamp/ID batches;
        # replay their scan rather than persisting an incompatible ID cursor.
        sql_order = any(hasattr(self.engine.source_adapter, name) for name in ('engine', 'snapshot', 'records'))
        after = checkpoint.get('last_id') if sql_order else None
        pages = metadata_pages(self.engine.source_adapter, stream, since, after,
                               min(self.settings.reconciliation_audit_batch_size,
                                   self.settings.reconciliation_max_files_per_run))
        try:
            for source_rows in pages:
                checkpoint['source_pages_read'] += 1
                source_rows = source_rows[:self.settings.reconciliation_max_files_per_run - visited]
                ids = [str(row['id']) for row in source_rows]
                with self.engine.uow() as repo:
                    columns = [model.id, model.source_origin] + [getattr(model, key)
                        for key in (*METADATA_FIELDS[stream], *(AUTH_FIELDS if stream == 'users' else ()))]
                    targets = {str(row['id']): dict(row) for row in repo.session.execute(
                        select(*columns).where(model.id.in_(ids))).mappings()}
                    if stream == 'resources':
                        subject_model = MODELS['resource_subjects']
                        grade_model = MODELS['resource_grade_levels']
                        subjects, grades = {}, {}
                        for resource_id, subject_id, primary in repo.session.execute(select(
                                subject_model.resource_id, subject_model.subject_id, subject_model.is_primary)
                                .where(subject_model.resource_id.in_(ids))):
                            subjects.setdefault(resource_id, []).append((subject_id, bool(primary)))
                        for resource_id, grade_id in repo.session.execute(select(grade_model.resource_id,
                                grade_model.grade_level_id).where(grade_model.resource_id.in_(ids))):
                            grades.setdefault(resource_id, []).append(grade_id)
                        for record_id, target in targets.items():
                            target['_resource_subjects'] = sorted(subjects.get(record_id, []))
                            target['_resource_grades'] = sorted(grades.get(record_id, []))
                results = []
                for source in source_rows:
                    record_id = str(source['id'])
                    target = targets.get(record_id)
                    if target and target.get('source_origin') == 'LOCAL':
                        continue
                    checkpoint['records_scanned'] += 1
                    if target is None:
                        results.append({'record_id': record_id, 'discrepancy_flag': 'ORPHAN_SOURCE'})
                        checkpoint['mismatched_count'] += 1
                        continue
                    fields = set(METADATA_FIELDS[stream])
                    compared = fields & source.keys() & target.keys()
                    different = metadata_differences(stream, source, target)
                    if stream == 'users':
                        fields.update(AUTH_FIELDS)
                        for key in AUTH_FIELDS:
                            if key in source and key in target:
                                compared.add(key)
                                if serialize_json_value(source[key]) != serialize_json_value(target[key]):
                                    different.append(key)
                    if stream == 'resources':
                        for key in ('_resource_subjects', '_resource_grades'):
                            fields.add(key)
                            if key in source and key in target:
                                compared.add(key)
                                if source[key] != target[key]:
                                    different.append(key)
                    if stream == 'users' and compared != fields:
                        checkpoint['auth_scope_complete'] = False
                        checkpoint['unsupported_fields'] = sorted(set(checkpoint.get('unsupported_fields', ())) | (fields - compared))
                    if different:
                        results.append({'record_id': record_id, 'discrepancy_flag': 'DESYNC_METADATA',
                            'source_value': {key: source[key] for key in different},
                            'target_value': {key: target[key] for key in different}})
                        checkpoint['mismatched_count'] += 1
                    elif compared != fields:
                        results.append({'record_id': record_id, 'discrepancy_flag': 'NOT_VERIFIED',
                            'target_value': {'reason_code': 'METADATA_UNSUPPORTED'}})
                        checkpoint['not_verified_count'] += 1
                    else:
                        checkpoint['matched_count'] += 1
                visited += len(source_rows)
                checkpoint.update(last_id=ids[-1] if sql_order else None, source_count=checkpoint['records_scanned'])
                if checkpoint.get('repair_requested'):
                    checkpoint['repair_pending'] = [r['record_id'] for r in results
                        if r['discrepancy_flag'] in ('ORPHAN_SOURCE', 'DESYNC_METADATA')]
                self._save(claim, checkpoint, results)
                self._repair_pending(claim, checkpoint)
                if visited >= self.settings.reconciliation_max_files_per_run or monotonic() - started >= self.settings.reconciliation_max_seconds_per_run:
                    complete = False
                    break
        finally:
            pages.close()  # Release the read-only SOURCE snapshot at each budget boundary.
        if complete:
            checkpoint.update(last_id=None, watermark_next=checkpoint['scan_started'])
            if (stream == 'users' and checkpoint.get('repair_requested') and not checkpoint['not_verified_count']
                    and checkpoint['auth_scope_complete']):
                checkpoint['auth_verified'] = True
        checkpoint['coverage_status'] = 'DELTA_VISITED' if complete else 'COVERAGE_BEHIND'
        self._save(claim, checkpoint, status='COMPLETED' if complete and not checkpoint['not_verified_count'] else 'PARTIAL')

    def _repair_pending(self, claim, checkpoint):
        ids = checkpoint.get('repair_pending', [])
        if not ids:
            return
        if self.sync is None:
            raise DomainError('AUDIT_REPAIR_WORKER_REQUIRED')
        self.sync.run_selected(claim['stream_name'], ids,
            fence=lambda session: self.queue.owned(session, claim))
        with self.engine.uow() as repo:
            self.queue.owned(repo.session, claim)
            rows = repo.session.scalars(select(Discrepancy).where(
                Discrepancy.run_id == claim['id'], Discrepancy.record_id.in_(ids),
                Discrepancy.discrepancy_flag.in_(('ORPHAN_SOURCE', 'DESYNC_METADATA'))))
            for row in rows:
                row.resolution_status = 'SYNCED'
        checkpoint['repaired_count'] = checkpoint.get('repaired_count', 0) + len(ids)
        checkpoint['repair_pending'] = []
        self._save(claim, checkpoint)

    def _ids(self, claim, lost):
        adapter = self.engine.source_adapter
        checkpoint = dict(claim['checkpoint'])
        checkpoint.setdefault('phase', 'SOURCE')
        checkpoint.setdefault('records_scanned', 0)
        checkpoint.setdefault('mismatched_count', 0)
        checkpoint.setdefault('not_verified_count', 0)
        self._save(claim, checkpoint)
        if not hasattr(adapter, 'snapshot'):
            raise DomainError('AUTHORITATIVE_SNAPSHOT_UNSUPPORTED')
        model = MODELS[STREAM_TABLE_MAPPING[claim['stream_name']]['target_table']]
        started = monotonic()
        visited, complete = 0, False
        while visited < self.settings.reconciliation_max_files_per_run and monotonic() - started < self.settings.reconciliation_max_seconds_per_run:
            if lost.is_set():
                raise DomainError('AUDIT_LEASE_LOST')
            results = []
            after = checkpoint.get('last_id')
            with adapter.snapshot() as reader:
                if hasattr(reader, 'connection'):
                    reader.connection.execute(text("SET LOCAL statement_timeout = '5000'"))
                summary = reader.reconciliation_summary(claim['stream_name'])
                if checkpoint.get('source_summary') and checkpoint['source_summary'] != summary:
                    raise DomainError('SOURCE_CHANGED_DURING_ID_CYCLE')
                checkpoint['source_summary'] = summary
                checkpoint['source_count'] = summary['count']
                size = min(self.settings.reconciliation_audit_batch_size,
                           self.settings.reconciliation_max_files_per_run - visited)
                if checkpoint['phase'] == 'SOURCE':
                    ids = reader.reconciliation_ids(claim['stream_name'], after, size)
                    if not ids:
                        if checkpoint.get('source_seen', 0) != summary['count']:
                            raise DomainError('SOURCE_COVERAGE_INVALID')
                        checkpoint.update(phase='TARGET', last_id=None)
                        continue
                    checkpoint['source_seen'] = checkpoint.get('source_seen', 0) + len(ids)
                    with self.engine.uow() as repo:
                        present = set(repo.session.scalars(select(model.id).where(model.id.in_(ids))))
                    for record_id in ids:
                        if record_id not in present:
                            results.append({'record_id': record_id, 'discrepancy_flag': 'ORPHAN_SOURCE'})
                else:
                    with self.engine.uow() as repo:
                        key = model.id.collate('C') if repo.session.get_bind().dialect.name == 'postgresql' else model.id
                        query = select(model.id).where(model.source_origin == 'POSTGRESQL').order_by(key).limit(size)
                        if after:
                            query = query.where(key > after)
                        ids = list(repo.session.scalars(query))
                    if not ids:
                        complete = True
                        break
                    present = reader.reconciliation_present(claim['stream_name'], ids)
                    missing = [record_id for record_id in ids if record_id not in present]
                    # A second authoritative snapshot must confirm absence, and consistent coverage.
                    if missing:
                        with adapter.snapshot() as fresh:
                            same_scope = fresh.reconciliation_summary(claim['stream_name']) == summary
                            reappeared = fresh.reconciliation_present(claim['stream_name'], missing)
                        for record_id in missing:
                            flag = 'ORPHAN_MASTER' if same_scope and record_id not in reappeared else 'NOT_VERIFIED'
                            results.append({'record_id': record_id, 'discrepancy_flag': flag})
                if ids != sorted(set(ids)) or (after and ids[0] <= after):
                    raise DomainError('SOURCE_COVERAGE_INVALID')
                visited += len(ids)
                checkpoint['records_scanned'] += len(ids)
                checkpoint['mismatched_count'] += sum(r['discrepancy_flag'] != 'NOT_VERIFIED' for r in results)
                checkpoint['not_verified_count'] += sum(r['discrepancy_flag'] == 'NOT_VERIFIED' for r in results)
                checkpoint['last_id'] = ids[-1]
            self._save(claim, checkpoint, results)
        if complete:
            checkpoint.update(last_id=None, phase='SOURCE', source_seen=0, source_summary=None,
                              generation=checkpoint.get('generation', 0) + 1)
        checkpoint['coverage_status'] = 'ID_SCOPE_VISITED' if complete else 'COVERAGE_BEHIND'
        self._save(claim, checkpoint, status='COMPLETED' if complete and not checkpoint['not_verified_count'] else 'PARTIAL')

    def coverage(self):
        verified, after = 0, None
        with self.engine.uow() as repo:
            total = repo.session.scalar(select(func.count()).select_from(ResourceVersion))
            while True:
                query = select(*[getattr(ResourceVersion, key) for key in FILE_COLUMNS],
                    MasterLearningResource.source_origin, FileState).join_from(ResourceVersion,
                    MasterLearningResource, MasterLearningResource.id == ResourceVersion.resource_id).join(
                    FileState, FileState.resource_version_id == ResourceVersion.id).where(
                    FileState.last_verified_at >= now() - timedelta(days=self.settings.reconciliation_deep_coverage_days)
                ).order_by(ResourceVersion.id).limit(self.settings.reconciliation_audit_batch_size)
                if after:
                    query = query.where(ResourceVersion.id > after)
                rows = repo.session.execute(query).all()
                if not rows:
                    break
                for row in rows:
                    version = SimpleNamespace(**dict(zip(FILE_COLUMNS, row[:len(FILE_COLUMNS)], strict=True)))
                    state = row[-1]
                    verified += bool(state.observed_identity == identity(version, row[-2])
                        and state.last_verified_hash == version.source_hash
                        and (state.status == 'SYNCED' or state.reason_code == 'SHA256_NOT_CHECKED'))
                after = rows[-1][0]
            return {'scheduler_enabled': self.settings.reconciliation_scheduler_enabled,
                'total_files': total, 'recent_sha256_evidence_count': verified,
                'not_verified_count': max(0, total - verified), 'io_concurrency': 1,
                'coverage_status': 'COVERAGE_BEHIND' if verified < total else 'RECENT_EVIDENCE',
                'whole_repository_verified': False}

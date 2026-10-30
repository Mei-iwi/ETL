from time import monotonic

from data_sync_etl.domain.core import DomainError, SyncAlreadyRunning, new_id, now
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.repositories import UnitOfWork
from data_sync_etl.ports.source import Cursor, SourceDataReader

STREAMS = {
    "grades": "master_grade_levels",
    "subjects": "subjects",
    "users": "master_users",
    "resources": "master_learning_resources",
}

SAFE_SYNC_ERROR_CODES = frozenset({
    'BLOCKED_MONGO_ID_MAPPING', 'BLOCKED_SOURCE_OWNERSHIP', 'BLOCKED_LOCAL_SOURCE_CHANGE',
    'BLOCKED_SOURCE_IDENTITY', 'BLOCKED_SOURCE_MAPPING',
})


class MasterSync:
    def __init__(self, source: SourceDataReader, uow: UnitOfWork, batch_size=100, metadata_projection=None,
                 skip_shared_metadata_validation=False):
        self.source, self.uow, self.batch_size = source, uow, batch_size
        self.metadata_projection = metadata_projection
        self.last_metadata_report = {}
        self.skip_shared_metadata_validation = skip_shared_metadata_validation

    def run(self, stream="all", full=False):
        if stream != "all" and stream not in STREAMS:
            raise DomainError("Unknown sync stream")
        with flow():
            with self.uow.sync_lock() as acquired:
                if not acquired:
                    raise SyncAlreadyRunning("Master data sync is already running")
                result = {
                    name: self._stream(name, full)
                    for name in (STREAMS if stream == "all" else [stream])
                }
            # PG sync lock/transaction is released before any Mongo RPC.
            if self.skip_shared_metadata_validation:
                event(stage='mongo_metadata_validation', action='SKIP_PG_SHARED_METADATA_VALIDATION')
                return result
            for name in result:
                self._project_metadata(name)
            return result

    def _project_metadata(self, stream):
        if (self.skip_shared_metadata_validation or stream not in ('users', 'resources')
                or self.metadata_projection is None):
            return
        try:
            self.last_metadata_report.update(self.metadata_projection.run(stream) or {})
        except Exception:
            self.last_metadata_report[stream] = {'status': 'FAILED', 'reason': 'RECONCILE_REQUIRED',
                                               'master_state': 'COMMITTED'}
            event(stage='master_metadata_projection', stream=stream, action='FAILED_RECONCILE_REQUIRED')

    def status(self, limit=20):
        with self.uow() as repo:
            runs = repo.find("sync_runs", order=("-started_at",), limit=limit)
        return {
            "runs": [
                {"run_id": run["id"], **{key: value for key, value in run.items() if key != "id"}}
                for run in runs
            ],
        }

    @staticmethod
    def cursor(checkpoint):
        if checkpoint and checkpoint["last_updated_at"] is not None:
            return Cursor(updated_at=checkpoint["last_updated_at"], id=checkpoint["last_id"])
        return None

    def run_selected(self, stream, record_ids, *, fence=None):
        """Repair exact identities without moving the global incremental cursor."""
        ids = sorted(set(record_ids))
        if stream not in STREAMS or len(ids) > 10000:
            raise DomainError('INVALID_REPAIR_SCOPE')
        if not ids:
            return {}
        if not hasattr(self.source, 'selected_batches'):
            raise DomainError('TARGETED_SOURCE_UNSUPPORTED')
        with self.uow.sync_lock() as acquired:
            if not acquired:
                raise SyncAlreadyRunning('Master data sync is already running')
            result = self._stream(stream, False, record_ids=ids, fence=fence)
        # Keep the existing projection policy; never perform Mongo RPC under the PG lock.
        self._project_metadata(stream)
        return result

    def _stream(self, stream, full, record_ids=None, fence=None):
        run_id, started = new_id(), monotonic()
        with self.uow() as repo:
            repo.ensure_checkpoint(stream)
            before = self.cursor(repo.get("sync_checkpoints", stream))
            repo.insert(
                "sync_runs",
                dict(
                    id=run_id,
                    stream_name=stream,
                    sync_mode="REPAIR" if record_ids is not None else "FULL" if full else "INCREMENTAL",
                    started_at=now(),
                    status="RUNNING",
                    records_read=0,
                    inserted=0,
                    updated=0,
                    skipped=0,
                    failed=0,
                    checkpoint_before=before.model_dump(mode="json") if before else None,
                    checkpoint_after=before.model_dump(mode="json") if before else None,
                ),
            )
        cursor = None if full else before
        totals = dict(records_read=0, inserted=0, updated=0, skipped=0, failed=0)
        try:
            batches = (self.source.selected_batches(stream, record_ids, self.batch_size)
                       if record_ids is not None else self.source.batches(stream, cursor, self.batch_size))
            seen = set()
            wanted = set(record_ids) if record_ids is not None else None
            for batch in batches:
                counts = dict(records_read=0, inserted=0, updated=0, skipped=0, failed=0)
                actions = []
                with self.uow() as repo:
                    if fence is not None:
                        fence(repo.session)
                    checkpoint = repo.get("sync_checkpoints", stream, lock=True)
                    for aggregate in batch:
                        if wanted is not None:
                            if aggregate.cursor.id not in wanted:
                                raise DomainError('INVALID_REPAIR_SCOPE')
                            seen.add(aggregate.cursor.id)
                        if getattr(self.source, 'source_origin', None) == 'POSTGRESQL':
                            locator = self.source.locator(stream, aggregate.cursor.id)
                            action = repo.sync_upsert(
                                STREAMS[stream], aggregate.values,
                                source_origin='POSTGRESQL', source_locator=locator,
                            )
                        else:
                            action = repo.sync_upsert(STREAMS[stream], aggregate.values)
                        if stream == "resources":
                            replaced = self._relations(repo, aggregate)
                            if replaced and action == "SKIPPED":
                                action = "UPDATED"
                        counts["records_read"] += 1
                        counts[action.lower()] += 1
                        actions.append(action)
                    next_cursor = batch[-1].cursor
                    existing = self.cursor(checkpoint)
                    if record_ids is None and (existing is None or next_cursor.key() > existing.key()):
                        repo.update(
                            "sync_checkpoints",
                            stream,
                            dict(
                                last_updated_at=next_cursor.updated_at,
                                last_id=next_cursor.id,
                                updated_at=now(),
                            ),
                        )
                    after = self.cursor(repo.get("sync_checkpoints", stream))
                    committed = {k: totals[k] + counts[k] for k in totals}
                    repo.update(
                        "sync_runs",
                        run_id,
                        {
                            **committed,
                            "checkpoint_after": after.model_dump(mode="json") if after else None,
                        },
                    )
                totals = committed
                for aggregate, action in zip(batch, actions, strict=True):
                    event(
                        run_id=run_id,
                        stream=stream,
                        source_id=aggregate.cursor.id,
                        target_id=aggregate.cursor.id,
                        action=action,
                        stage="sync",
                        duration_ms=int((monotonic() - started) * 1000),
                    )
            if wanted is not None and seen != wanted:
                raise DomainError('REPAIR_SOURCE_CHANGED')
            with self.uow() as repo:
                repo.update("sync_runs", run_id, dict(status="COMPLETED", finished_at=now()))
        except Exception as exc:
            code = str(exc).split(':', 1)[0] if isinstance(exc, DomainError) else None
            safe_code = code if code in SAFE_SYNC_ERROR_CODES else None
            with self.uow() as repo:
                repo.update(
                    "sync_runs",
                    run_id,
                    dict(
                        status="FAILED",
                        failed=1,
                        finished_at=now(),
                        error_message=safe_code or "Sync batch failed; inspect source contract and references",
                    ),
                )
            raise DomainError(safe_code or "Sync failed; committed checkpoints are preserved") from None
        return {"run_id": run_id, **totals}

    @staticmethod
    def _relations(repo, aggregate):
        resource_id = aggregate.values["id"]
        changed = False
        for table, rows in [
            ("resource_subjects", aggregate.subjects),
            ("resource_grade_levels", aggregate.grades),
        ]:
            desired = [row.model_dump(exclude={"updated_at"}) for row in rows]
            current = repo.find(table, {"resource_id": resource_id})

            def key(row):
                return tuple(sorted(row.items()))

            if len({key(row) for row in desired}) != len(desired):
                raise DomainError("Duplicate source relation")
            if sorted(map(key, current)) != sorted(map(key, desired)):
                repo.delete(table, {"resource_id": resource_id})
                for row in desired:
                    repo.insert(table, row)
                changed = True
        return changed

from time import monotonic

from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.repositories import UnitOfWork
from data_sync_etl.ports.source import Cursor, SourceDataReader

STREAMS = {
    "grades": "master_grade_levels",
    "subjects": "subjects",
    "users": "master_users",
    "resources": "master_learning_resources",
}


class MasterSync:
    def __init__(self, source: SourceDataReader, uow: UnitOfWork, batch_size=100):
        self.source, self.uow, self.batch_size = source, uow, batch_size

    def run(self, stream="all", full=False):
        if stream != "all" and stream not in STREAMS:
            raise DomainError("Unknown sync stream")
        with flow():
            return {
                name: self._stream(name, full)
                for name in (STREAMS if stream == "all" else [stream])
            }

    @staticmethod
    def cursor(checkpoint):
        if checkpoint and checkpoint["last_updated_at"] is not None:
            return Cursor(updated_at=checkpoint["last_updated_at"], id=checkpoint["last_id"])
        return None

    def _stream(self, stream, full):
        run_id, started = new_id(), monotonic()
        with self.uow() as repo:
            repo.ensure_checkpoint(stream)
            before = self.cursor(repo.get("sync_checkpoints", stream))
            repo.insert(
                "sync_runs",
                dict(
                    id=run_id,
                    stream_name=stream,
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
            for batch in self.source.batches(stream, cursor, self.batch_size):
                counts = dict(records_read=0, inserted=0, updated=0, skipped=0, failed=0)
                actions = []
                with self.uow() as repo:
                    checkpoint = repo.get("sync_checkpoints", stream, lock=True)
                    for aggregate in batch:
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
                    if existing is None or next_cursor.key() > existing.key():
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
            with self.uow() as repo:
                repo.update("sync_runs", run_id, dict(status="COMPLETED", finished_at=now()))
        except Exception:
            with self.uow() as repo:
                repo.update(
                    "sync_runs",
                    run_id,
                    dict(
                        status="FAILED",
                        failed=1,
                        finished_at=now(),
                        error_message="Sync batch failed; inspect source contract and references",
                    ),
                )
            raise DomainError("Sync failed; committed checkpoints are preserved") from None
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

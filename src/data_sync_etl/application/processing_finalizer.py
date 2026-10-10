from datetime import timedelta

from data_sync_etl.application.processing_state import processing_state, projection_fingerprint
from data_sync_etl.domain.core import now
from data_sync_etl.logging import event
from data_sync_etl.ports.repositories import UnitOfWork


class ProcessingFinalizer:
    def __init__(self, uow: UnitOfWork, post_process, projector, max_attempts=3, retry_seconds=30):
        self.uow = uow
        self.postprocess = post_process
        self.projector = projector
        self.max_attempts = max_attempts
        self.retry_seconds = retry_seconds

    def finalize(self, job_id: str) -> dict | None:
        with self.uow() as repo:
            job = repo.get("ocr_jobs", job_id)
        if job is None or job["status"] != "COMPLETED":
            return None
        version_id = job["resource_version_id"]
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                return {"status": "IN_PROGRESS"}
            return self._finalize_locked(job_id, version_id)

    def _finalize_locked(self, job_id, version_id):
        with self.uow() as repo:
            job = repo.get("ocr_jobs", job_id)
            if (
                job["status"] != "COMPLETED"
                or job["failed_pages"]
                or job["completed_pages"] != job["total_pages"]
            ):
                return None
            if processing_state(repo, job) == "CONTENT_READY":
                return {"status": "ALREADY_COMPLETED"}
            attempts = repo.find(
                "etl_stage_runs",
                {
                    "resource_version_id": version_id,
                    "stage": "MONGO_PROJECTION",
                },
                order=("-created_at", "-id"),
            )

            retry_requests = repo.find(
                "etl_stage_runs",
                {
                    "resource_version_id": version_id,
                    "stage": "FINALIZATION_RETRY",
                    "status": "PENDING",
                },
                order=("-created_at", "-id"),
                limit=1,
            )
            retry_request = retry_requests[0] if retry_requests else None
            # The request records the number of projection attempts preceding
            # this manual cycle, so timestamp ties cannot change its boundary.
            baseline = int(retry_request["input_fingerprint"], 16) if retry_request else 0
            if baseline > len(attempts):
                raise RuntimeError("Invalid finalization retry boundary")
            cycle_attempts = attempts[: len(attempts) - baseline]

            # The database lock is the liveness authority. Once acquired, any
            # RUNNING audit row has lost its owner; no timeout can evict a live owner.
            abandoned = [s for s in attempts if s["status"] == "RUNNING"]
            for old in abandoned:
                repo.update(
                    "etl_stage_runs",
                    old["id"],
                    {
                        "status": "FAILED",
                        "finished_at": now(),
                        "error_message": "Finalization owner disconnected; recovered by worker",
                    },
                )
            failures = 0
            for attempt in cycle_attempts:
                if attempt["status"] == "COMPLETED":
                    break
                failures += 1
            if failures >= self.max_attempts:
                if retry_request is not None:
                    repo.update(
                        "etl_stage_runs",
                        retry_request["id"],
                        {
                            "status": "FAILED",
                            "finished_at": now(),
                            "error_message": "Manual finalization retry exhausted",
                        },
                    )
                return {"status": "RETRY_EXHAUSTED"}
            if (
                cycle_attempts
                and not abandoned
                and cycle_attempts[0]["status"] == "FAILED"
                and cycle_attempts[0]["finished_at"]
                and now() < cycle_attempts[0]["finished_at"] + timedelta(seconds=self.retry_seconds)
            ):
                return {"status": "RETRY_WAIT"}
            stage = repo.insert(
                "etl_stage_runs",
                {
                    "resource_version_id": version_id,
                    "stage": "MONGO_PROJECTION",
                    "status": "RUNNING",
                    "started_at": now(),
                    "created_at": now(),
                },
            )
            posts = repo.find(
                "etl_stage_runs",
                {
                    "resource_version_id": version_id,
                    "stage": "POSTPROCESS",
                },
                order=("-created_at", "-id"),
                limit=1,
            )
            post = posts[0] if posts and posts[0]["status"] == "COMPLETED" else None

        event(stage="finalization", job_id=job_id, action="STARTED", run_id=stage["id"])
        try:
            if post is None:
                self.postprocess.run_locked(version_id)
                with self.uow() as repo:
                    post = repo.find(
                        "etl_stage_runs",
                        {
                            "resource_version_id": version_id,
                            "stage": "POSTPROCESS",
                        },
                        order=("-created_at", "-id"),
                        limit=1,
                    )[0]
            result = self.projector.project(version_id, job_id, stage["id"], len(attempts) + 1)
            with self.uow() as repo:
                current = repo.get("etl_stage_runs", stage["id"], lock=True)
                if current["status"] != "RUNNING":
                    raise RuntimeError("Finalization ownership lost")
                repo.update(
                    "etl_stage_runs",
                    stage["id"],
                    {
                        "status": "COMPLETED",
                        "finished_at": now(),
                        "input_fingerprint": projection_fingerprint(job_id, post),
                    },
                )
                if retry_request is not None:
                    repo.update(
                        "etl_stage_runs",
                        retry_request["id"],
                        {"status": "COMPLETED", "finished_at": now()},
                    )
        except Exception:
            with self.uow() as repo:
                repo.update(
                    "etl_stage_runs",
                    stage["id"],
                    {
                        "status": "FAILED",
                        "finished_at": now(),
                        "error_message": "Finalization failed; retry from persisted PostgreSQL data",
                    },
                )
                if retry_request is not None and failures + 1 >= self.max_attempts:
                    repo.update(
                        "etl_stage_runs",
                        retry_request["id"],
                        {
                            "status": "FAILED",
                            "finished_at": now(),
                            "error_message": "Manual finalization retry exhausted",
                        },
                    )
            event(stage="finalization", job_id=job_id, action="FAILED", run_id=stage["id"])
            raise
        event(stage="finalization", job_id=job_id, action="COMPLETED", run_id=stage["id"])
        return {"status": "CONTENT_READY", "resource_version_id": version_id, **result}

    def reconcile_once(self) -> bool:
        # Completed OCR jobs are durable pending work, including the crash gap
        # between the last page commit and the terminal callback.
        with self.uow() as repo:
            jobs = repo.find("ocr_jobs", {"status": "COMPLETED"}, order=("created_at", "id"))
        for job in jobs:
            try:
                result = self.finalize(job["id"])
            except Exception:
                event(stage="finalization", job_id=job["id"], action="RECONCILE_FAILED")
                return True
            if result and result["status"] == "CONTENT_READY":
                return True
        return False

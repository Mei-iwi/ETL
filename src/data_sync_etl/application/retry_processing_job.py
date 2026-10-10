"""Persist a manual retry request without OCR or MongoDB I/O in HTTP."""

from data_sync_etl.application.processing_state import processing_state
from data_sync_etl.domain.core import ProcessingJobNotFound, ProcessingRetryConflict, now
from data_sync_etl.ports.repositories import UnitOfWork


class RetryProcessingJob:
    def __init__(self, uow: UnitOfWork, max_manual_retries: int = 3):
        self.uow = uow
        self.max_manual_retries = max_manual_retries

    @staticmethod
    def _result(job: dict, action: str, processing_status: str) -> dict:
        version_id = job["resource_version_id"]
        return {
            "job_id": job["id"],
            "resource_version_id": version_id,
            "job_status": job["status"],
            "processing_status": processing_status,
            "action": action,
            "status_url": f"/admin/resource-versions/{version_id}/etl-status",
        }

    def run(self, job_id: str) -> dict:
        with self.uow() as repo:
            job = repo.get("ocr_jobs", job_id)
        if job is None:
            raise ProcessingJobNotFound("Processing job not found")

        version_id = job["resource_version_id"]
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                raise ProcessingRetryConflict("Processing job is busy")
            with self.uow() as repo:
                job = repo.get("ocr_jobs", job_id)
                if job is None:
                    raise ProcessingJobNotFound("Processing job not found")
                version = repo.get("resource_versions", version_id)
                resource = (
                    repo.get("master_learning_resources", version["resource_id"])
                    if version is not None
                    else None
                )
                if (
                    version is None
                    or resource is None
                    or not resource["is_active"]
                    or resource["deleted_at"] is not None
                ):
                    raise ProcessingRetryConflict("Learning resource is unavailable")

                state = processing_state(repo, job)
                if job["status"] in ("PENDING", "RUNNING"):
                    return self._result(job, "ALREADY_IN_PROGRESS", state)
                if state == "CONTENT_READY":
                    return self._result(job, "ALREADY_COMPLETED", state)
                if job["status"] in ("FAILED", "COMPLETED_WITH_ERRORS"):
                    return self._retry_ocr(repo, job)
                if job["status"] == "COMPLETED":
                    return self._retry_finalization(repo, job, state)
                raise ProcessingRetryConflict("Processing job cannot be retried")

    def _check_manual_limit(self, repo, version_id: str) -> None:
        count = sum(
            repo.count(
                "etl_stage_runs",
                {"resource_version_id": version_id, "stage": stage},
            )
            for stage in ("OCR_RETRY", "FINALIZATION_RETRY")
        )
        if count >= self.max_manual_retries:
            raise ProcessingRetryConflict("Manual retry limit reached")

    def _retry_ocr(self, repo, job: dict) -> dict:
        # Worker success/failure takes task lock before job lock. Use the same
        # order to avoid a PostgreSQL deadlock with a finishing OCR worker.
        tasks = repo.find("ocr_page_tasks", {"job_id": job["id"]}, order=("page_num",), lock=True)
        current = repo.get("ocr_jobs", job["id"], lock=True)
        if current["status"] not in ("FAILED", "COMPLETED_WITH_ERRORS"):
            raise ProcessingRetryConflict("OCR job state changed")
        failed = [task for task in tasks if task["status"] == "FAILED"]
        if (
            not failed
            or len(tasks) != current["total_pages"]
            or any(task["status"] not in ("SUCCEEDED", "FAILED") for task in tasks)
            or sum(task["status"] == "SUCCEEDED" for task in tasks) != current["completed_pages"]
            or len(failed) != current["failed_pages"]
        ):
            raise ProcessingRetryConflict("OCR job state is inconsistent")
        self._check_manual_limit(repo, current["resource_version_id"])

        stamp = now()
        for task in failed:
            repo.update(
                "ocr_page_tasks",
                task["id"],
                {
                    "status": "PENDING",
                    # New manual cycle grants fresh automatic attempts. The
                    # OCR_RETRY stage is the durable manual counter.
                    "retry_count": 0,
                    "worker_id": None,
                    "claim_token": None,
                    "heartbeat_at": None,
                    "started_at": None,
                    "completed_at": None,
                    "error_message": None,
                    "local_image_path": None,
                    "updated_at": stamp,
                },
            )
        repo.update(
            "ocr_jobs",
            current["id"],
            {"status": "PENDING", "failed_pages": 0, "completed_at": None, "updated_at": stamp},
        )
        repo.insert(
            "etl_stage_runs",
            {
                "resource_version_id": current["resource_version_id"],
                "stage": "OCR_RETRY",
                "status": "COMPLETED",
                "started_at": stamp,
                "finished_at": stamp,
                "created_at": stamp,
                "error_message": "Requeued failed OCR pages: "
                + ",".join(str(task["page_num"]) for task in failed),
            },
        )
        return self._result(repo.get("ocr_jobs", current["id"]), "OCR_REQUEUED", "OCR_PENDING")

    def _retry_finalization(self, repo, job: dict, state: str) -> dict:
        version_id = job["resource_version_id"]
        pending = repo.find(
            "etl_stage_runs",
            {"resource_version_id": version_id, "stage": "FINALIZATION_RETRY", "status": "PENDING"},
            limit=1,
        )
        if pending:
            return self._result(job, "ALREADY_QUEUED", "FINALIZATION_PENDING")
        if state in ("FINALIZING", "FINALIZATION_PENDING"):
            return self._result(job, "ALREADY_IN_PROGRESS", state)
        if state != "FINALIZATION_FAILED":
            raise ProcessingRetryConflict("Finalization is not retryable")

        tasks = repo.find("ocr_page_tasks", {"job_id": job["id"]})
        if (
            len(tasks) != job["total_pages"]
            or any(task["status"] != "SUCCEEDED" for task in tasks)
            or job["failed_pages"]
            or job["completed_pages"] != job["total_pages"]
        ):
            raise ProcessingRetryConflict("OCR job state is inconsistent")
        self._check_manual_limit(repo, version_id)
        previous_attempts = repo.count(
            "etl_stage_runs", {"resource_version_id": version_id, "stage": "MONGO_PROJECTION"}
        )
        stamp = now()
        repo.insert(
            "etl_stage_runs",
            {
                "resource_version_id": version_id,
                "stage": "FINALIZATION_RETRY",
                "status": "PENDING",
                "started_at": stamp,
                "created_at": stamp,
                # Stable cycle boundary, unaffected by timestamp ties.
                "input_fingerprint": f"{previous_attempts:064x}",
            },
        )
        return self._result(job, "FINALIZATION_REQUEUED", "FINALIZATION_PENDING")

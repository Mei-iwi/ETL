from data_sync_etl.application.processing_state import processing_state
from data_sync_etl.domain.core import ProcessingJobNotFound
from data_sync_etl.domain.format_validation import PUBLIC_PROCESSING_ERROR_CODES
from data_sync_etl.ports.repositories import UnitOfWork


class GetProcessingJob:
    def __init__(self, uow: UnitOfWork):
        self.uow = uow

    def run(self, job_id: str) -> dict:
        with self.uow() as repo:
            job = repo.get("ocr_jobs", job_id)
            if job is None:
                raise ProcessingJobNotFound("Processing job not found")

            stages = repo.find(
                "etl_stage_runs",
                {"resource_version_id": job["resource_version_id"]},
                order=("-created_at", "-id"),
                limit=20,
            )
            version = repo.get("resource_versions", job["resource_version_id"])
            unsupported = version["storage_object_key"].lower().endswith(('.mp3', '.mp4'))
            failures = repo.find('ocr_page_tasks', {'job_id': job_id, 'status': 'FAILED'}, limit=1)
            error_code = failures[0]['error_message'] if failures else None

            return {
                "id": job["id"],
                "error_code": "UNSUPPORTED_PROCESSING_FORMAT" if unsupported else error_code if error_code in PUBLIC_PROCESSING_ERROR_CODES else None,
                "resource_version_id": job["resource_version_id"],
                "status": job["status"],
                "processing_status": processing_state(repo, job),
                "total_pages": job["total_pages"],
                "completed_pages": job["completed_pages"],
                "failed_pages": job["failed_pages"],
                "started_at": job["started_at"],
                "completed_at": job["completed_at"],
                "updated_at": job["updated_at"],
                "stage_runs": stages,
            }

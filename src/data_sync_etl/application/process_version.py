from data_sync_etl.application.processing_state import processing_state
from data_sync_etl.domain.core import DomainError, ResourceVersionNotFound
from data_sync_etl.ports.repositories import UnitOfWork


class ProcessResourceVersion:
    def __init__(self, uow: UnitOfWork, ocr):
        self.uow = uow
        self.ocr = ocr

    def run(self, version_id: str) -> dict:
        with self.uow() as repo:
            version = repo.get("resource_versions", version_id)
            if version is None:
                raise ResourceVersionNotFound("Resource version not found")
            resource = repo.get(
                "master_learning_resources",
                version["resource_id"],
            )

            if resource is None or not resource["is_active"] or resource["deleted_at"] is not None:
                raise DomainError("Learning resource is unavailable")

        job = self.ocr.create_job(version_id)

        with self.uow() as repo:
            state = processing_state(repo, job)

        actions = {
            "PENDING": "QUEUED",
            "RUNNING": "IN_PROGRESS",
            "COMPLETED": "WAITING_FOR_FINALIZATION",
            "COMPLETED_WITH_ERRORS": "REQUIRES_RETRY",
            "FAILED": "REQUIRES_RETRY",
        }

        action = actions.get(job["status"], "UNKNOWN")
        if state == "CONTENT_READY":
            action = "ALREADY_COMPLETED"

        return {
            "resource_version_id": version_id,
            "job_id": job["id"],
            "job_status": job["status"],
            "processing_status": state,
            "action": action,
            "status_url": (f"/admin/resource-versions/{version_id}/etl-status"),
        }

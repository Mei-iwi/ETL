from data_sync_etl.domain.core import DomainError
from data_sync_etl.logging import flow


class EtlOrchestrator:
    def __init__(self, sync, ingestion, ocr, postprocess, uow):
        self.sync, self.ingestion, self.ocr, self.postprocess, self.uow = (
            sync,
            ingestion,
            ocr,
            postprocess,
            uow,
        )

    def start(self, resource_id, bucket, object_key, sync_master=False):
        with flow():
            if sync_master:
                self.sync.run(full=False)
            result = self.ingestion.run(resource_id, bucket, object_key)
            if result["action"] == "NO_CHANGE":
                return result
            job = self.ocr.create_job(result["version"]["id"])
            return {**result, "job": job}

    def resume(self, version_id):
        """Schedule missing OCR or process a terminal job; never run a worker in HTTP."""
        with flow():
            job = self.ocr.create_job(version_id)
            if job["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
                return self.postprocess.run(version_id)
            return {"action": "WAITING_FOR_WORKER", "job": job}

    def status(self, version_id):
        with self.uow() as repo:
            version = repo.get("resource_versions", version_id)
            if not version:
                raise DomainError("Resource version not found")
            resource = repo.get("master_learning_resources", version["resource_id"])
            latest = repo.find(
                "resource_versions",
                {"resource_id": resource["id"]},
                order=("-version_number",),
                limit=1,
            )[0]
            jobs = repo.find(
                "ocr_jobs",
                {"resource_version_id": version_id},
                order=("-created_at", "-id"),
                limit=1,
            )
            stages = repo.find(
                "etl_stage_runs",
                {"resource_version_id": version_id},
                order=("-created_at",),
                limit=20,
            )
            return {
                "resource": resource,
                "version": version,
                "latest_version": latest,
                "ocr_job": jobs[0] if jobs else None,
                "stage_runs": stages,
                "content_unit_count": repo.count(
                    "content_units", {"resource_version_id": version_id}
                ),
            }

    def content_units(self, version_id, *, offset=0, limit=50, search=""):
        with self.uow() as repo:
            if not repo.get("resource_versions", version_id):
                raise DomainError("Resource version not found")
            return repo.content_units(version_id, offset=offset, limit=limit, search=search)

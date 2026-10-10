from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.ports.document_store import ContentDocumentPort
from data_sync_etl.ports.repositories import UnitOfWork


class MongoContentProjector:
    def __init__(
        self,
        uow: UnitOfWork,
        documents: ContentDocumentPort,
    ) -> None:
        self.uow = uow
        self.documents = documents

    def _assert_owner(self, version_id: str, stage_id: str | None) -> None:
        if stage_id is None:
            return
        with self.uow() as repo:
            stage = repo.get("etl_stage_runs", stage_id)
            latest = repo.find(
                "etl_stage_runs",
                {"resource_version_id": version_id, "stage": "MONGO_PROJECTION"},
                order=("-created_at", "-id"),
                limit=1,
            )
        if not stage or stage["status"] != "RUNNING" or not latest or latest[0]["id"] != stage_id:
            raise DomainError("Finalization ownership lost")

    def project(
        self,
        version_id: str,
        job_id: str,
        stage_id: str | None = None,
        generation: int | None = None,
    ) -> dict:
        with self.uow() as repo:
            version = repo.get("resource_versions", version_id)

            job = repo.get("ocr_jobs", job_id)

            if version is None or job is None:
                raise DomainError("Processing data not found")

            if job["resource_version_id"] != version_id:
                raise DomainError("OCR job does not match resource version")
            if (
                job["status"] != "COMPLETED"
                or job["failed_pages"]
                or job["completed_pages"] != job["total_pages"]
            ):
                raise DomainError("Projection requires all OCR pages to succeed")

            tasks = repo.find(
                "ocr_page_tasks",
                {"job_id": job_id},
                order=("page_num",),
            )

            units = repo.find(
                "content_units",
                {"resource_version_id": version_id},
                order=("sequence_no",),
            )

        page_count = 0
        if len(tasks) != job["total_pages"] or any(t["status"] != "SUCCEEDED" for t in tasks):
            raise DomainError("Projection requires every OCR page result")
        unit_count = 0

        for task in tasks:
            if task["status"] != "SUCCEEDED":
                continue

            with self.uow() as repo:
                results = repo.find(
                    "ocr_page_results",
                    {"page_task_id": task["id"]},
                    limit=1,
                )

            if not results:
                raise DomainError("Completed OCR page has no result")

            result = results[0]
            stamp = now()
            self._assert_owner(version_id, stage_id)

            self.documents.upsert_ocr_page(
                {
                    "_id": f"{job_id}:{task['page_num']}",
                    "job_id": job_id,
                    "resource_version_id": version_id,
                    "page_num": task["page_num"],
                    "char_count": len(result["raw_text"]),
                    "raw_text": result["raw_text"],
                    "confidence": float(result["confidence"])
                    if result["confidence"] is not None
                    else None,
                    "engine_name": result["engine_name"],
                    "engine_version": result["engine_version"],
                    "source_checksum": version["source_hash"],
                    "created_at": stamp,
                    "updated_at": stamp,
                    **({"projection_generation": generation} if generation is not None else {}),
                }
            )

            page_count += 1

        for unit in units:
            with self.uow() as repo:
                body = repo.get("content_unit_texts", unit["id"])

            if body is None:
                raise DomainError("Content unit text not found")

            stamp = now()
            self._assert_owner(version_id, stage_id)

            self.documents.upsert_content_unit(
                {
                    "_id": unit["id"],
                    "content_unit_id": unit["id"],
                    "page_result_id": body["page_result_id"],
                    "is_current": True,
                    "resource_version_id": version_id,
                    "title": unit["title"],
                    "page_from": unit["page_from"],
                    "page_to": unit["page_to"],
                    "text": body["text"],
                    "markdown": body["text"],
                    "keywords": [],
                    "created_at": stamp,
                    "updated_at": stamp,
                    **({"projection_generation": generation} if generation is not None else {}),
                }
            )

            unit_count += 1

        self._assert_owner(version_id, stage_id)
        self.documents.reconcile_content_units(
            version_id,
            [unit["id"] for unit in units],
            generation,
        )

        return {
            "ocr_pages": page_count,
            "content_units": unit_count,
        }

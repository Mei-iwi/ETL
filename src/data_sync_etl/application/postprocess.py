import hashlib

from data_sync_etl.domain.core import DomainError, normalize_text, now
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.repositories import UnitOfWork


class Postprocessor:
    def __init__(self, uow: UnitOfWork, normalizer=normalize_text):
        self.uow, self.normalizer = uow, normalizer

    def run(self, version_id, *, allow_partial=False):
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                raise DomainError("Postprocess already running for this resource version")
            return self.run_locked(version_id, allow_partial=allow_partial)

    def run_locked(self, version_id, *, allow_partial=False):
        """Run only while the caller holds the version's processing lock."""
        with flow():
            with self.uow() as repo:
                if not repo.get("resource_versions", version_id):
                    raise DomainError("Resource version not found")
                stage = repo.insert(
                    "etl_stage_runs",
                    dict(
                        resource_version_id=version_id,
                        stage="POSTPROCESS",
                        status="RUNNING",
                        started_at=now(),
                        created_at=now(),
                    ),
                )
            try:
                with self.uow() as repo:
                    repo.get("resource_versions", version_id, lock=True)
                    jobs = repo.find(
                        "ocr_jobs",
                        {"resource_version_id": version_id},
                        order=("-created_at", "-id"),
                        limit=1,
                    )
                    if not jobs or jobs[0]["status"] not in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
                        raise DomainError("Postprocess requires a completed OCR job")
                    job = jobs[0]
                    if not allow_partial and (
                        job["status"] != "COMPLETED"
                        or job["failed_pages"]
                        or job["completed_pages"] != job["total_pages"]
                    ):
                        raise DomainError("Postprocess requires all OCR pages to succeed")
                    digest = hashlib.sha256(b"page-text-nfc-v1")
                    count, successful = 0, 0
                    # One page/result at a time, keeping large document text out of a whole-file buffer.
                    for page in range(1, job["total_pages"] + 1):
                        tasks = repo.find(
                            "ocr_page_tasks", {"job_id": job["id"], "page_num": page}, limit=1
                        )
                        if not tasks:
                            raise DomainError("OCR page task is missing")
                        task = tasks[0]
                        digest.update(f"{page}:{task['status']}:".encode())
                        if task["status"] != "SUCCEEDED":
                            if not allow_partial:
                                raise DomainError("OCR page has not succeeded")
                            continue
                        successful += 1
                        results = repo.find(
                            "ocr_page_results", {"page_task_id": task["id"]}, limit=1
                        )
                        if not results:
                            raise DomainError("OCR page result is missing")
                        result = results[0]
                        normalized = self.normalizer(result["raw_text"])
                        digest.update(hashlib.sha256(normalized.encode()).digest())
                        if result["normalized_text"] != normalized:
                            repo.update(
                                "ocr_page_results",
                                result["id"],
                                {"normalized_text": normalized, "updated_at": now()},
                            )
                        if not normalized:
                            continue
                        count += 1
                        units = repo.find(
                            "content_units",
                            {"resource_version_id": version_id, "sequence_no": count},
                            limit=1,
                        )
                        if units:
                            unit = units[0]
                            body = repo.get("content_unit_texts", unit["id"])
                            if (
                                unit["page_from"] != page
                                or body["text"] != normalized
                                or body["page_result_id"] != result["id"]
                            ):
                                repo.update(
                                    "content_units",
                                    unit["id"],
                                    dict(
                                        page_from=page,
                                        page_to=page,
                                        review_status="UNREVIEWED",
                                        retrieval_eligible=False,
                                        updated_at=now(),
                                    ),
                                )
                                repo.update(
                                    "content_unit_texts",
                                    unit["id"],
                                    dict(text=normalized, page_result_id=result["id"]),
                                )
                        else:
                            unit = repo.insert(
                                "content_units",
                                dict(
                                    resource_version_id=version_id,
                                    unit_type="PAGE_TEXT",
                                    sequence_no=count,
                                    page_from=page,
                                    page_to=page,
                                    review_status="UNREVIEWED",
                                    retrieval_eligible=False,
                                ),
                            )
                            repo.insert(
                                "content_unit_texts",
                                dict(
                                    content_unit_id=unit["id"],
                                    page_result_id=result["id"],
                                    text=normalized,
                                ),
                            )
                    # Remove obsolete trailing baseline units without materializing document text.
                    sequence = count + 1
                    while units := repo.find(
                        "content_units",
                        {"resource_version_id": version_id, "sequence_no": sequence},
                        limit=1,
                    ):
                        repo.delete("content_unit_texts", {"content_unit_id": units[0]["id"]})
                        repo.delete("content_units", {"id": units[0]["id"]})
                        sequence += 1
                    fingerprint = digest.hexdigest()
                    # No postprocess queue exists. Leave the legacy enqueue timestamp
                    # untouched; etl_stage_runs records actual start/finish/status.
                    repo.update(
                        "etl_stage_runs",
                        stage["id"],
                        dict(status="COMPLETED", finished_at=now(), input_fingerprint=fingerprint),
                    )
                coverage = {
                    "successful_pages": successful,
                    "total_pages": job["total_pages"],
                    "failed_pages": job["failed_pages"],
                }
                event(
                    resource_version_id=version_id,
                    job_id=job["id"],
                    stage="postprocess",
                    action="PARTIAL_COVERAGE" if job["failed_pages"] else "COMPLETED",
                    coverage=coverage,
                )
                return {
                    "processing_status": "PARTIAL_CONTENT"
                    if job["failed_pages"]
                    else "POSTPROCESSED",
                    "content_unit_count": count,
                    "coverage": coverage,
                    "input_fingerprint": fingerprint,
                }
            except Exception:
                with self.uow() as repo:
                    repo.update(
                        "etl_stage_runs",
                        stage["id"],
                        dict(
                            status="FAILED",
                            finished_at=now(),
                            error_message="Postprocess failed; existing content preserved",
                        ),
                    )
                raise DomainError("Postprocess failed; existing content preserved") from None

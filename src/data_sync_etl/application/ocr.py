from datetime import timedelta

from data_sync_etl.domain.core import DomainError, job_terminal, new_id, now
from data_sync_etl.logging import event
from data_sync_etl.ports.processing import OCREnginePort, OCRResult, PDFProcessorPort, StoragePort
from data_sync_etl.ports.repositories import UnitOfWork


class OCRPipeline:
    def __init__(
        self,
        uow: UnitOfWork,
        storage: StoragePort,
        pdf: PDFProcessorPort,
        engine: OCREnginePort,
        max_retries=3,
        heartbeat_timeout=120,
    ):
        self.uow, self.storage, self.pdf, self.engine = uow, storage, pdf, engine
        self.max_retries, self.heartbeat_timeout = max_retries, heartbeat_timeout

    def create_job(self, version_id, triggered_by=None):
        with self.uow() as repo:
            version = repo.get("resource_versions", version_id, lock=True)
            if version is None:
                raise DomainError("Resource version not found")
            # Reuse any existing run, including terminal jobs: repeated commands are idempotent.
            existing = repo.find(
                "ocr_jobs",
                {"resource_version_id": version_id},
                order=("-created_at", "-id"),
                limit=1,
            )
            if existing:
                return existing[0]
            path = self.storage.get_local_path(
                version["storage_bucket"], version["storage_object_key"]
            )
            try:
                total = self.pdf.page_count(path)
            except Exception:
                raise DomainError("Cannot open resource as a readable PDF") from None
            if total < 1:
                raise DomainError("Empty PDF has no processable pages")
            job = repo.insert(
                "ocr_jobs",
                dict(
                    resource_version_id=version_id,
                    triggered_by=triggered_by,
                    status="PENDING",
                    total_pages=total,
                    completed_pages=0,
                    failed_pages=0,
                ),
            )
            for page in range(1, total + 1):
                repo.insert(
                    "ocr_page_tasks",
                    dict(job_id=job["id"], page_num=page, status="PENDING", retry_count=0),
                )
            return job

    def claim(self, worker_id):
        if not worker_id or len(worker_id) > 100:
            raise DomainError("Worker ID must have 1 to 100 characters")
        with self.uow() as repo:
            rows = repo.find(
                "ocr_page_tasks",
                {"status": "PENDING"},
                order=("created_at", "id"),
                limit=1,
                lock=True,
                skip_locked=True,
            )
            if not rows:
                return None
            task = rows[0]
            stamp = now()
            change = dict(
                status="RUNNING",
                worker_id=worker_id,
                claim_token=new_id(),
                started_at=stamp,
                heartbeat_at=stamp,
                updated_at=stamp,
                error_message=None,
            )
            repo.update("ocr_page_tasks", task["id"], change)
            job = repo.get("ocr_jobs", task["job_id"], lock=True)
            repo.update(
                "ocr_jobs",
                job["id"],
                dict(status="RUNNING", started_at=job["started_at"] or stamp, updated_at=stamp),
            )
            return {**task, **change}

    @staticmethod
    def owns(current, claimed):
        return (
            current
            and current["status"] == "RUNNING"
            and current["claim_token"] == claimed["claim_token"]
        )

    def heartbeat(self, claimed):
        with self.uow() as repo:
            current = repo.get("ocr_page_tasks", claimed["id"], lock=True)
            if not self.owns(current, claimed):
                return False
            repo.update("ocr_page_tasks", claimed["id"], {"heartbeat_at": now()})
            return True

    def recognize(self, task):
        with self.uow() as repo:
            job = repo.get("ocr_jobs", task["job_id"])
            version = repo.get("resource_versions", job["resource_version_id"])
        path = self.storage.get_local_path(version["storage_bucket"], version["storage_object_key"])
        native = self.pdf.extract_native_text(path, task["page_num"])
        # Conservative native-text gate; empty/image-only pages need an OCR adapter.
        printable = sum(c.isprintable() or c.isspace() for c in native)
        if native.strip() and printable / max(len(native), 1) >= 0.98 and "\ufffd" not in native:
            return OCRResult(
                native, engine_name="native_pdf_text", metadata={"page_num": task["page_num"]}
            ), None
        image = self.storage.image_path(task["id"], task["claim_token"])
        self.pdf.render_page_to_image(path, task["page_num"], image)
        return self.engine.recognize(image), str(image)

    def succeed(self, claimed, result, image_path=None):
        with self.uow() as repo:
            task = repo.get("ocr_page_tasks", claimed["id"], lock=True)
            if not self.owns(task, claimed):
                return False
            repo.insert(
                "ocr_page_results",
                dict(
                    page_task_id=task["id"],
                    raw_text=result.text,
                    confidence=result.confidence,
                    engine_name=result.engine_name,
                    engine_version=result.engine_version,
                    result_metadata={
                        **result.metadata,
                        "page_num": task["page_num"],
                        "job_id": task["job_id"],
                    },
                ),
            )
            repo.update(
                "ocr_page_tasks",
                task["id"],
                dict(
                    status="SUCCEEDED",
                    completed_at=now(),
                    updated_at=now(),
                    local_image_path=image_path,
                    claim_token=None,
                ),
            )
            self._counter(repo, task["job_id"], completed=1)
        event(task_id=task["id"], job_id=task["job_id"], stage="ocr", action="SUCCEEDED")
        return True

    def _fail(self, repo, task, message):
        attempts = task["retry_count"] + 1
        terminal = attempts >= self.max_retries
        repo.update(
            "ocr_page_tasks",
            task["id"],
            dict(
                retry_count=attempts,
                status="FAILED" if terminal else "PENDING",
                error_message=message,
                worker_id=None,
                claim_token=None,
                heartbeat_at=None,
                updated_at=now(),
                completed_at=now() if terminal else None,
            ),
        )
        if terminal:
            self._counter(repo, task["job_id"], failed=1)

    def fail(self, claimed):
        with self.uow() as repo:
            task = repo.get("ocr_page_tasks", claimed["id"], lock=True)
            if not self.owns(task, claimed):
                return False
            self._fail(repo, task, "OCR attempt failed; verify PDF and OCR engine configuration")
        event(task_id=task["id"], stage="ocr", action="ATTEMPT_FAILED")
        return True

    @staticmethod
    def _counter(repo, job_id, completed=0, failed=0):
        job = repo.get("ocr_jobs", job_id, lock=True)
        completed += job["completed_pages"]
        failed += job["failed_pages"]
        status = job_terminal(completed, failed, job["total_pages"])
        repo.update(
            "ocr_jobs",
            job_id,
            dict(
                completed_pages=completed,
                failed_pages=failed,
                status=status,
                updated_at=now(),
                completed_at=now() if status != "RUNNING" else None,
            ),
        )

    def recover_stale(self):
        recovered = 0
        while True:
            with self.uow() as repo:
                tasks = repo.stale_tasks(now() - timedelta(seconds=self.heartbeat_timeout))
                for task in tasks:
                    self._fail(repo, task, "Worker lease expired")
                recovered += len(tasks)
            if not tasks:
                return {"recovered": recovered}

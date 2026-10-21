from contextlib import nullcontext
from datetime import timedelta
from functools import partial
from types import SimpleNamespace

from data_sync_etl.domain.core import DomainError, SourceFileError, job_terminal, new_id, now
from data_sync_etl.domain.file_integrity import stream_hash
from data_sync_etl.domain.format_validation import FormatValidatorRegistry, ProcessingFormatError
from data_sync_etl.logging import event
from data_sync_etl.ports.processing import (
    DocumentExtractorPort,
    OCREnginePort,
    OCRResult,
    PDFProcessorPort,
    StoragePort,
)
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
        documents: DocumentExtractorPort | None = None,
        formats=None,
    ):
        self.uow, self.storage, self.pdf, self.engine = uow, storage, pdf, engine
        self.max_retries, self.heartbeat_timeout = max_retries, heartbeat_timeout
        self.formats = formats or FormatValidatorRegistry()
        self.native_documents = documents

    def create_job(self, version_id, triggered_by=None):
        reference = self.resolver.resolve(version_id)
        if reference.ownership == 'LOCAL_OWNED':
            # Preserve LOCAL command idempotency even if its managed file later disappears.
            with self.uow() as repo:
                jobs = repo.find('ocr_jobs', {'resource_version_id': version_id},
                                 order=('-created_at', '-id'), limit=1)
            if jobs:
                return jobs[0]
        with self.resolver.temporary_path(reference) as path:
            return self._create_job(version_id, triggered_by, path)

    def _create_job(self, version_id, triggered_by, path):
        with self.uow() as repo:
            version = repo.get("resource_versions", version_id, lock=True)
            if version is None:
                raise DomainError("Resource version not found")
            resource = repo.get('master_learning_resources', version['resource_id'], lock=True)
            if resource is None or not resource['is_active'] or resource['deleted_at'] is not None:
                raise DomainError('Resource inactive or deleted')
            if version["storage_object_key"].lower().endswith(('.mp3', '.mp4')):
                raise ProcessingFormatError('UNSUPPORTED_PROCESSING_FORMAT')
            # Reuse any existing run, including terminal jobs: repeated commands are idempotent.
            existing = repo.find(
                "ocr_jobs",
                {"resource_version_id": version_id},
                order=("-created_at", "-id"),
                limit=1,
            )
            if existing:
                return existing[0]
            validator = self.formats.find_validator(path.name)
            if not validator.text_extraction_supported:
                raise ProcessingFormatError('UNSUPPORTED_PROCESSING_FORMAT')
            if validator.ocr_requires_runtime:
                if not getattr(self.engine, 'supports_images', False):
                    raise ProcessingFormatError('BLOCKED_OCR_RUNTIME')
                try:
                    self.engine.check_runtime()
                except DomainError:
                    raise ProcessingFormatError('BLOCKED_OCR_RUNTIME') from None
            if path.suffix.lower() == '.pdf':
                try:
                    total = self.pdf.page_count(path)
                except Exception:
                    raise DomainError("Cannot open resource as a readable PDF") from None
            else:
                if self.native_documents is None:
                    raise ProcessingFormatError('UNSUPPORTED_PROCESSING_FORMAT')
                # One bounded document task; provenance is carried by logical units,
                # never by fabricated PDF page numbers.
                total = 1
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
        if version['storage_object_key'].lower().endswith(('.mp3', '.mp4')):
            raise ProcessingFormatError('UNSUPPORTED_PROCESSING_FORMAT')
        reference = self.resolver.resolve(version['id'])
        with self.resolver.temporary_path(reference) as path:
            # Source images share the verified attempt directory, avoiding a second slot.
            scope = nullcontext(path.parent if reference.ownership == 'SOURCE_BACKED' else None)
            with scope as directory:
                result, image = self._recognize_path(task, version, path, directory)
            if reference.ownership == 'SOURCE_BACKED':
                image = None  # source scratch is never retained in task metadata
        return result, image

    def _recognize_path(self, task, version, path, source_directory=None):
        engine = self.engine
        if source_directory is not None and hasattr(engine, 'recognize_scoped'):
            engine = SimpleNamespace(
                supports_images=engine.supports_images,
                recognize=partial(engine.recognize_scoped, directory=source_directory),
            )
        if path.suffix.lower() in ('.mp3', '.mp4'):
            raise ProcessingFormatError('UNSUPPORTED_PROCESSING_FORMAT')
        if path.suffix.lower() in ('.docx', '.xlsx', '.pptx'):
            with path.open('rb') as stream:
                observed_hash = stream_hash(stream)
            if observed_hash != version['source_hash']:
                raise ProcessingFormatError('SOURCE_HASH_MISMATCH')
        if path.suffix.lower() != '.pdf':
            validator = self.formats.find_validator(path.name)
            validator.validate_path(path)
            if validator.ocr_requires_runtime:
                image = source_directory / 'page.png' if source_directory else self.storage.image_path(task['id'], task['claim_token'])
                return self.native_documents.recognize_image(path, image, engine)
            return self.native_documents.extract(path), None
        native = self.pdf.extract_native_text(path, task["page_num"])
        # Conservative native-text gate; empty/image-only pages need an OCR adapter.
        printable = sum(c.isprintable() or c.isspace() for c in native)
        valid_native = (
            bool(native.strip())
            and printable / max(len(native), 1) >= 0.98
            and "\ufffd" not in native
        )
        # A short text layer can be a header over a large scanned page image.
        mixed_scan = (
            valid_native
            and len(native.strip()) < 32
            and self.pdf.has_significant_image(path, task["page_num"])
        )
        if valid_native and not mixed_scan:
            return OCRResult(
                native, engine_name="native_pdf_text", metadata={"page_num": task["page_num"]}
            ), None
        image = source_directory / 'page.png' if source_directory else self.storage.image_path(task["id"], task["claim_token"])
        self.pdf.render_page_to_image(path, task["page_num"], image)
        try:
            result = engine.recognize(image)
            if not result.text.strip() and not mixed_scan:
                raise DomainError('EMPTY_EXTRACTED_CONTENT')
        except Exception:
            image.unlink(missing_ok=True)
            raise
        if mixed_scan:
            result = OCRResult(
                native + "\n" + result.text,
                confidence=result.confidence,
                engine_name=result.engine_name,
                engine_version=result.engine_version,
                metadata={**result.metadata, "mixed_native_text": True,
                          "ocr_empty": not bool(result.text.strip())},
            )
        return result, str(image)

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

    def _fail(self, repo, task, message, permanent=False):
        attempts = task["retry_count"] + 1
        terminal = permanent or attempts >= self.max_retries
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

    def fail(self, claimed, error=None):
        with self.uow() as repo:
            task = repo.get("ocr_page_tasks", claimed["id"], lock=True)
            if not self.owns(task, claimed):
                return False
            code = str(error).split(':', 1)[0] if isinstance(error, DomainError) else ''
            permanent_codes = {'INVALID_DOCUMENT_CONTENT', 'EMPTY_EXTRACTED_CONTENT',
                               'EXTRACTION_LIMIT_EXCEEDED', 'INVALID_CSV', 'UNSUPPORTED_TEXT_ENCODING',
                               'INVALID_IMAGE', 'INVALID_OPENXML_CONTAINER', 'OPENXML_LIMIT_EXCEEDED'}
            permanent_codes.add('UNSUPPORTED_PROCESSING_FORMAT')
            permanent_codes.add('SOURCE_HASH_MISMATCH')
            if isinstance(error, SourceFileError):
                self._fail(repo, task, error.code, permanent=not error.transient)
            elif code in permanent_codes or code == 'BLOCKED_OCR_RUNTIME':
                self._fail(repo, task, code, permanent=code in permanent_codes)
            else:
                self._fail(repo, task, "OCR attempt failed; verify document and OCR engine configuration")
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

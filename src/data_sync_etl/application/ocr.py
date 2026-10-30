from contextlib import nullcontext
from datetime import timedelta
from functools import partial
from types import SimpleNamespace

from data_sync_etl.domain.core import DomainError, SourceFileError, job_terminal, new_id, now
from data_sync_etl.domain.file_integrity import stream_hash
from data_sync_etl.domain.format_validation import (
    PUBLIC_PROCESSING_ERROR_CODES,
    FormatValidatorRegistry,
    ProcessingFormatError,
)
from data_sync_etl.domain.resource_deletion import resource_deleted
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
        media=None,
        default_target_language='vi',
        max_active=1,
    ):
        self.uow, self.storage, self.pdf, self.engine = uow, storage, pdf, engine
        self.max_retries, self.heartbeat_timeout = max_retries, heartbeat_timeout
        self.formats = formats or FormatValidatorRegistry()
        self.native_documents = documents
        self.media = media
        self.default_target_language = default_target_language
        self.max_active = max_active

    def create_job(self, version_id, triggered_by=None, *, fence=None):
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id)
            resource = repo.get('master_learning_resources', version['resource_id']) if version else None
            source_backed = bool(resource and resource.get('source_origin') != 'LOCAL')
            if version and version.get('lifecycle_status') == 'METADATA_ONLY' and not source_backed:
                raise DomainError('CONTENT_NOT_AVAILABLE')
        reference = self.resolver.resolve(version_id)
        if reference.ownership == 'SOURCE_BACKED':
            with self.resolver.open_verified_stream(reference, index=True) as stream:
                data = stream.read()
            return self._create_source_job(version_id, triggered_by, reference, data, fence=fence)
        if reference.ownership == 'LOCAL_OWNED':
            # Preserve LOCAL command idempotency even if its managed file later disappears.
            with self.uow() as repo:
                jobs = repo.find('ocr_jobs', {'resource_version_id': version_id},
                                 order=('-created_at', '-id'), limit=1)
            if jobs:
                return jobs[0]
        with self.resolver.temporary_path(reference) as path:
            return self._create_job(version_id, triggered_by, path, fence=fence)

    def _create_source_job(self, version_id, triggered_by, reference, data, *, fence=None):
        from pathlib import PurePosixPath
        filename = reference.filename
        extension = PurePosixPath(filename).suffix.lower()
        if extension in ('.mp3', '.mp4'):
            raise ProcessingFormatError('UNSUPPORTED_SOURCE_IN_MEMORY_FORMAT')
        validator = self.formats.find_validator(filename)
        if not validator.text_extraction_supported:
            raise ProcessingFormatError('UNSUPPORTED_PROCESSING_FORMAT')
        if validator.ocr_requires_runtime:
            if not getattr(self.engine, 'supports_images', False) or not callable(getattr(self.engine, 'recognize_bytes', None)):
                raise ProcessingFormatError('BLOCKED_OCR_RUNTIME')
            try:
                self.engine.check_runtime()
            except DomainError:
                raise ProcessingFormatError('BLOCKED_OCR_RUNTIME') from None
        if extension == '.pdf':
            import pymupdf
            try:
                with pymupdf.open(stream=data, filetype='pdf') as document:
                    if document.needs_pass:
                        raise ProcessingFormatError('UNSUPPORTED_PROCESSING_FORMAT')
                    total = len(document)
            except ProcessingFormatError:
                raise
            except Exception:
                raise DomainError('INVALID_DOCUMENT_CONTENT') from None
        elif extension in ('.docx', '.xlsx', '.pptx', '.txt', '.csv') or validator.ocr_requires_runtime:
            total = 1
        else:
            raise ProcessingFormatError('UNSUPPORTED_SOURCE_IN_MEMORY_FORMAT')
        if total < 1 or total > 2000:
            raise ProcessingFormatError('EXTRACTION_LIMIT_EXCEEDED')
        # Recheck exact mapping/revision after processing, outside a MASTER transaction.
        current_reference = self.resolver.resolve(version_id)
        if current_reference != reference:
            raise DomainError('SOURCE_VERSION_MUTATED')
        from hashlib import sha256
        if current_reference.expected_sha256 != sha256(data).hexdigest():
            raise DomainError('SOURCE_HASH_MISMATCH')
        # SOURCE I/O and validation finish before opening the MASTER transaction.
        with self.uow() as repo:
            if fence is not None:
                fence(repo)
            version = repo.get('resource_versions', version_id, lock=True)
            resource = repo.get('master_learning_resources', version['resource_id'], lock=True) if version else None
            if (version is None or resource is None or not resource['is_active'] or resource_deleted(resource)
                    or version['source_hash'].lower() != current_reference.expected_sha256):
                raise DomainError('SOURCE_VERSION_MUTATED')
            existing = repo.find('ocr_jobs', {'resource_version_id': version_id},
                                 order=('-created_at', '-id'), limit=1)
            if existing:
                return existing[0]
            job = repo.insert('ocr_jobs', dict(resource_version_id=version_id, triggered_by=triggered_by,
                status='PENDING', total_pages=total, completed_pages=0, failed_pages=0))
            repo.update('resource_versions', version_id, {
                'lifecycle_status': 'INGESTED',
                'processing_options': {**(version.get('processing_options') or {}),
                                       'content_availability': 'PROCESSING', 'translation_pending': True}})
            imports = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
            if len(imports) != 1:
                raise DomainError('BLOCKED_SOURCE_MAPPING')
            repo.update('source_file_imports', imports[0]['id'], {
                'status': 'PROCESSING', 'last_error_code': None, 'processing_job_id': job['id']})
            for page in range(1, total + 1):
                repo.insert('ocr_page_tasks', dict(job_id=job['id'], page_num=page,
                                                   status='PENDING', retry_count=0))
            if fence is not None:
                fence(repo)
            return job

    def _create_job(self, version_id, triggered_by, path, *, fence=None):
        with self.uow() as repo:
            if fence is not None:
                fence(repo)
            version = repo.get("resource_versions", version_id, lock=True)
            if version is None:
                raise DomainError("Resource version not found")
            resource = repo.get('master_learning_resources', version['resource_id'], lock=True)
            if resource is None or not resource['is_active'] or resource_deleted(resource):
                raise DomainError('Resource inactive or deleted')
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
            repo.update('resource_versions', version_id, {'processing_options': {
                **(version.get('processing_options') or {}), 'translation_pending': True}})
            for page in range(1, total + 1):
                repo.insert(
                    "ocr_page_tasks",
                    dict(job_id=job["id"], page_num=page, status="PENDING", retry_count=0),
                )
            if fence is not None:
                fence(repo)
            return job

    def claim(self, worker_id):
        if not worker_id or len(worker_id) > 100:
            raise DomainError("Worker ID must have 1 to 100 characters")
        with self.uow() as repo:
            rows = repo.pending_live_ocr_tasks(max_active=self.max_active, heartbeat_timeout=self.heartbeat_timeout)
            if not rows:
                return None
            task = rows[0]
            job = repo.get('ocr_jobs', task['job_id'])
            version = repo.get('resource_versions', job['resource_version_id'])
            resource = repo.get('master_learning_resources', version['resource_id'], lock=True)
            if resource_deleted(resource):
                return None
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
            return {**task, **change, 'deletion_revision': resource['deletion_revision']}

    def schedule_authorized_source_job(self, *, limit=50):
        """Enqueue one opted-in SOURCE version without copying its original file."""
        if self.resolver.source_registry is None:
            return False
        enabled = {item.source_id for item in self.resolver.source_registry.registrations()
                   if item.enabled and item.capabilities.read_content
                   and item.capabilities.process_content and item.capabilities.index_derived_content}
        if not enabled:
            return False
        with self.uow() as repo:
            candidates = repo.find('source_file_imports', {'status': 'METADATA_ONLY'},
                                   order=('updated_at', 'id'), limit=limit)
            rows = []
            for imported in candidates:
                source_id = imported['source_system']
                if source_id == 'postgresql' and len(enabled) == 1:
                    source_id = next(iter(enabled))
                if source_id not in enabled or not imported.get('target_version_id'):
                    continue
                version = repo.get('resource_versions', imported['target_version_id'])
                resource = repo.get('master_learning_resources', imported['resource_id'])
                if (version is None or resource is None or resource.get('source_origin') == 'LOCAL'
                        or version.get('lifecycle_status') != 'METADATA_ONLY'
                        or not resource.get('is_active') or resource_deleted(resource)
                        or resource.get('publication_status') != 'PUBLISHED'
                        or imported.get('last_error_code') not in (None, 'SOURCE_METADATA_ONLY')):
                    continue
                if (version.get('processing_options') or {}).get('source_processing_status') == 'UNSUPPORTED':
                    continue
                # Existing jobs are claimed/finalized through their normal queue;
                # returning success here repeatedly would starve both queues.
                if repo.find('ocr_jobs', {'resource_version_id': version['id']}, limit=1):
                    continue
                rows.append((version['id'], version.get('processing_options') or {}))
        for version_id, options in rows:
            try:
                self.create_job(version_id)
                return True
            except SourceFileError as exc:
                if exc.code not in ('SOURCE_CAPABILITY_BLOCKED', 'SOURCE_CAPABILITY_INVALID'):
                    continue
            except ProcessingFormatError as exc:
                with self.uow() as repo:
                    current = repo.get('resource_versions', version_id, lock=True)
                    if current and current.get('lifecycle_status') == 'METADATA_ONLY':
                        repo.update('resource_versions', version_id, {'processing_options': {
                            **options, 'source_processing_status': 'UNSUPPORTED',
                            'source_processing_error': exc.code}})
        return False

    def owns(self, current, claimed):
        return (
            current
            and current["status"] == "RUNNING"
            and current["claim_token"] == claimed["claim_token"]
            and current['heartbeat_at'] is not None
            and current['heartbeat_at'] > now() - timedelta(seconds=self.heartbeat_timeout)
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
        reference = self.resolver.resolve(version['id'])
        if reference.ownership == 'SOURCE_BACKED':
            with self.resolver.open_verified_stream(reference, index=True) as stream:
                payload = stream.read()
            result = self._recognize_bytes(task, version, reference.filename, payload)
            from data_sync_etl.application.bilingual import paired_result
            options = {'target_language': self.default_target_language, **(version.get('processing_options') or {})}
            return paired_result(result, version['id'], task['page_num'], options), None
        with self.resolver.temporary_path(reference) as path:
            # Source images share the verified attempt directory, avoiding a second slot.
            scope = nullcontext(path.parent if reference.ownership == 'SOURCE_BACKED' else None)
            with scope as directory:
                result, image = self._recognize_path(task, version, path, directory)
            if reference.ownership == 'SOURCE_BACKED':
                image = None  # source scratch is never retained in task metadata
        from data_sync_etl.application.bilingual import paired_result
        options = {'target_language': self.default_target_language, **(version.get('processing_options') or {})}
        return paired_result(result, version['id'], task['page_num'], options), image

    def _recognize_bytes(self, task, version, filename, payload):
        from pathlib import PurePosixPath
        extension = PurePosixPath(filename).suffix.lower()
        if extension == '.pdf':
            import pymupdf
            try:
                with pymupdf.open(stream=payload, filetype='pdf') as document:
                    page = document[task['page_num'] - 1]
                    native = page.get_text()
                    printable = sum(c.isprintable() or c.isspace() for c in native)
                    valid = bool(native.strip()) and printable / max(len(native), 1) >= .98 and '\ufffd' not in native
                    mixed = valid and len(native.strip()) < 32 and bool(page.get_image_info())
                    if valid and not mixed:
                        return OCRResult(native, engine_name='native_pdf_text',
                                         metadata={'page_num': task['page_num']})
                    if not callable(getattr(self.engine, 'recognize_bytes', None)):
                        raise ProcessingFormatError('BLOCKED_OCR_RUNTIME')
                    scale = min(1.0, 2048 / max(page.rect.width, page.rect.height))
                    rendered = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes('png')
                    result = self.engine.recognize_bytes(rendered)
                    if not result.text.strip() and not mixed:
                        raise DomainError('EMPTY_EXTRACTED_CONTENT')
                    if mixed:
                        result = OCRResult(native + '\n' + result.text, confidence=result.confidence,
                            engine_name=result.engine_name, engine_version=result.engine_version,
                            metadata={**result.metadata, 'mixed_native_text': True,
                                      'ocr_empty': not bool(result.text.strip())})
                    return result
            except (DomainError, ProcessingFormatError):
                raise
            except Exception:
                raise DomainError('INVALID_DOCUMENT_CONTENT') from None
        validator = self.formats.find_validator(filename)
        if validator.ocr_requires_runtime:
            from io import BytesIO

            from PIL import Image, ImageOps
            if not callable(getattr(self.engine, 'recognize_bytes', None)):
                raise ProcessingFormatError('BLOCKED_OCR_RUNTIME')
            try:
                with Image.open(BytesIO(payload)) as image:
                    if image.width < 1 or image.height < 1 or image.width * image.height > 16_000_000:
                        raise DomainError('EXTRACTION_LIMIT_EXCEEDED')
                    with ImageOps.exif_transpose(image) as oriented:
                        with oriented.convert('RGB') as rgb:
                            output = BytesIO()
                            rgb.save(output, format='PNG')
                result = self.engine.recognize_bytes(output.getvalue())
                output.close()
                if not result.text.strip():
                    raise DomainError('EMPTY_EXTRACTED_CONTENT')
                from data_sync_etl.adapters.extraction import MAX_TEXT_CHARS, MAX_UNIT_CHARS
                if len(result.text) > MAX_TEXT_CHARS:
                    raise DomainError('EXTRACTION_LIMIT_EXCEEDED')
                return OCRResult(result.text, result.confidence, result.engine_name, result.engine_version,
                    {**result.metadata, 'source_type': 'image', 'logical_units': [
                        {'start': start, 'end': min(start + MAX_UNIT_CHARS, len(result.text)),
                         'title': 'Ảnh · OCR chữ', 'locator': {'kind': 'image', 'char_offset': start}}
                        for start in range(0, len(result.text), MAX_UNIT_CHARS)]})
            except DomainError:
                raise
            except Exception:
                raise DomainError('INVALID_IMAGE') from None
        return self.native_documents.extract_bytes(
            filename, payload,
            max_uncompressed_bytes=getattr(self.resolver.settings,
                                           'source_content_max_uncompressed_bytes', 128 * 1024 * 1024))

    def _recognize_path(self, task, version, path, source_directory=None):
        engine = self.engine
        if source_directory is not None and hasattr(engine, 'recognize_scoped'):
            engine = SimpleNamespace(
                supports_images=engine.supports_images,
                recognize=partial(engine.recognize_scoped, directory=source_directory),
            )
        if path.suffix.lower() in ('.mp3', '.mp4'):
            if self.media is None:
                raise ProcessingFormatError('MEDIA_RUNTIME_UNAVAILABLE')
            with path.open('rb') as stream:
                if stream_hash(stream) != version['source_hash']:
                    raise ProcessingFormatError('SOURCE_HASH_MISMATCH')
            from data_sync_etl.application.media_capacity import media_capacity
            settings = self.media.settings
            with media_capacity(self.uow, settings.media_max_concurrency, settings.media_timeout_seconds):
                return self.media.extract(path, version.get('processing_options') or {}), None
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
        with self.uow() as check_repo:
            job_check = check_repo.get('ocr_jobs', claimed['job_id'])
            version_check = check_repo.get('resource_versions', job_check['resource_version_id']) if job_check else None
            resource_check = check_repo.get('master_learning_resources', version_check['resource_id']) if version_check else None
        if resource_check and resource_check.get('source_origin') != 'LOCAL':
            reference = self.resolver.resolve(version_check['id'])
            self.resolver.require_content_capabilities(reference, index=True)
        with self.uow() as repo:
            task = repo.get("ocr_page_tasks", claimed["id"], lock=True)
            if not self.owns(task, claimed):
                return False
            job = repo.get('ocr_jobs', task['job_id'])
            version = repo.get('resource_versions', job['resource_version_id'])
            resource = repo.get('master_learning_resources', version['resource_id'], lock=True)
            if resource_deleted(resource) or resource['deletion_revision'] != claimed.get('deletion_revision', resource['deletion_revision']):
                repo.update('ocr_page_tasks', task['id'], {'status': 'PENDING', 'worker_id': None,
                    'claim_token': None, 'heartbeat_at': None, 'error_message': 'RESOURCE_SOFT_DELETED'})
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
            job = repo.get('ocr_jobs', task['job_id'])
            version = repo.get('resource_versions', job['resource_version_id'])
            resource = repo.get('master_learning_resources', version['resource_id'], lock=True)
            if resource_deleted(resource) or resource['deletion_revision'] != claimed.get('deletion_revision', resource['deletion_revision']):
                repo.update('ocr_page_tasks', task['id'], {'status': 'PENDING', 'worker_id': None,
                    'claim_token': None, 'heartbeat_at': None, 'error_message': 'RESOURCE_SOFT_DELETED'})
                return True
            code = str(error).split(':', 1)[0] if isinstance(error, DomainError) else ''
            if code == 'MEDIA_CAPACITY_TIMEOUT':
                # Waiting for a cluster-wide model slot is backpressure, not an
                # extraction failure. Keep the task pending without spending retries.
                repo.update('ocr_page_tasks', task['id'], {'status': 'PENDING', 'worker_id': None,
                    'claim_token': None, 'heartbeat_at': None, 'error_message': 'MEDIA_CAPACITY_WAIT'})
                return True
            permanent_codes = {'INVALID_DOCUMENT_CONTENT', 'EMPTY_EXTRACTED_CONTENT',
                               'EXTRACTION_LIMIT_EXCEEDED', 'INVALID_CSV', 'UNSUPPORTED_TEXT_ENCODING',
                               'INVALID_IMAGE', 'INVALID_OPENXML_CONTAINER', 'OPENXML_LIMIT_EXCEEDED'}
            permanent_codes.add('UNSUPPORTED_PROCESSING_FORMAT')
            permanent_codes.add('SOURCE_HASH_MISMATCH')
            permanent_codes.update({'INVALID_MEDIA', 'MEDIA_DURATION_EXCEEDED', 'MEDIA_SIZE_EXCEEDED', 'INVALID_MEDIA_TRACK'})
            if isinstance(error, SourceFileError):
                self._fail(repo, task, error.code, permanent=not error.transient)
            elif code in permanent_codes or code in PUBLIC_PROCESSING_ERROR_CODES:
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
        if status == 'FAILED':
            version = repo.get('resource_versions', job['resource_version_id'])
            repo.update('resource_versions', version['id'], {'processing_options': {
                **(version.get('processing_options') or {}), 'translation_pending': False,
                'translation_state': 'extraction_failed'}})
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

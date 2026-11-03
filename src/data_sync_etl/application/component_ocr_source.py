"""Bind component page execution to the existing verified source resolver."""
from contextlib import contextmanager
from pathlib import PurePosixPath

from data_sync_etl.application.bilingual import paired_result
from data_sync_etl.application.component_ocr_page import ComponentOCRPageExecutor
from data_sync_etl.domain.core import DomainError


class ComponentOCRSourceBridge:
    """Use only server-owned mappings and registered SOURCE capabilities."""

    def __init__(self, queue, content, resolver, trusted_mapping, legacy_ocr):
        self.queue, self.content, self.resolver = queue, content, resolver
        self.trusted_mapping, self.legacy_ocr = trusted_mapping, legacy_ocr

    def _registration(self, reference):
        source_id = self.resolver._capability_source_id(reference)
        registrations = [entry for entry in self.resolver.source_registry.registrations()
                         if entry.source_id == source_id and entry.enabled]
        if len(registrations) != 1:
            raise DomainError('COMPONENT_SOURCE_IDENTITY_MISMATCH')
        return registrations[0]

    def _reference(self, snapshot, source_version_id, source_hash):
        if not self.trusted_mapping('page', snapshot):
            raise DomainError('COMPONENT_CONTENT_UNAUTHORIZED')
        reference = self.resolver.resolve(snapshot['master_version_id'])
        if (reference.ownership != 'SOURCE_BACKED' or reference.source_version_id != source_version_id
                or reference.expected_sha256 != source_hash):
            raise DomainError('COMPONENT_SOURCE_IDENTITY_MISMATCH')
        if self._registration(reference).namespace != snapshot['namespace']:
            raise DomainError('COMPONENT_SOURCE_IDENTITY_MISMATCH')
        self.resolver.require_content_capabilities(reference, index=True)
        return reference

    def submit_version(self, master_version_id, *, generation, processing_fingerprint):
        """Schedule a server-resolved SOURCE version; reuse manifest before file I/O.

        Generation and fingerprint must come from trusted processing policy,
        never from the browser. This method is not registered as an API yet.
        """
        reference = self.resolver.resolve(master_version_id)
        if reference.ownership != 'SOURCE_BACKED' or not reference.source_version_id:
            raise DomainError('COMPONENT_SOURCE_IDENTITY_MISMATCH')
        namespace = self._registration(reference).namespace
        snapshot = {'master_version_id': master_version_id, 'namespace': namespace}
        self._reference(snapshot, reference.source_version_id, reference.expected_sha256)
        manifest = self.content.get_manifest(namespace=namespace, version_id=reference.source_version_id,
                                             generation=generation)
        if manifest is not None:
            if (manifest['source_hash'] != reference.expected_sha256
                    or manifest['fingerprint'] != processing_fingerprint):
                raise DomainError('COMPONENT_MANIFEST_STALE')
            total_pages = manifest['total_pages']
        else:
            extension = PurePosixPath(reference.filename).suffix.lower()
            validator = self.legacy_ocr.formats.find_validator(reference.filename)
            if not validator.text_extraction_supported or extension in ('.mp3', '.mp4'):
                raise DomainError('UNSUPPORTED_SOURCE_IN_MEMORY_FORMAT')
            if extension == '.pdf':
                import pymupdf

                with self.resolver.open_verified_stream(reference, index=True) as stream:
                    try:
                        with pymupdf.open(stream=stream.read(), filetype='pdf') as document:
                            if document.needs_pass:
                                raise DomainError('INVALID_DOCUMENT_CONTENT')
                            total_pages = len(document)
                    except DomainError:
                        raise
                    except Exception:
                        raise DomainError('INVALID_DOCUMENT_CONTENT') from None
            else:
                total_pages = 1
        job_id = self.queue.submit(
            namespace=namespace, source_version_id=reference.source_version_id,
            master_version_id=master_version_id, master_resource_id=reference.resource_id,
            generation=generation, source_hash=reference.expected_sha256,
            processing_fingerprint=processing_fingerprint, total_pages=total_pages,
        )
        if manifest is None:
            self.content.put_manifest(namespace=namespace, version_id=reference.source_version_id,
                                      generation=generation, total_pages=total_pages,
                                      source_hash=reference.expected_sha256, fingerprint=processing_fingerprint)
        return job_id

    def authorize(self, snapshot, source_version_id, source_hash):
        self._reference(snapshot, source_version_id, source_hash)
        return True

    @contextmanager
    def open_for_task(self, task):
        reference = self._reference(task['component_snapshot'], task['source_version_id'], task['source_hash'])
        with self.resolver.open_verified_stream(reference, index=True) as stream:
            yield stream

    def recognize_for_task(self, task, stream, page_num):
        reference = self._reference(task['component_snapshot'], task['source_version_id'], task['source_hash'])
        with self.legacy_ocr.uow() as repo:
            version = repo.get('resource_versions', task['component_snapshot']['master_version_id'])
        if (not version or version.get('source_hash', '').lower() != task['source_hash']):
            raise DomainError('COMPONENT_SOURCE_IDENTITY_MISMATCH')
        result = self.legacy_ocr._recognize_bytes({'page_num': page_num}, version, reference.filename, stream.read())
        options = {'target_language': self.legacy_ocr.default_target_language,
                   **(version.get('processing_options') or {})}
        return paired_result(result, version['id'], page_num, options)

    @staticmethod
    def _missing_context(*_args):
        raise DomainError('COMPONENT_TASK_CONTEXT_REQUIRED')

    def executor(self):
        return ComponentOCRPageExecutor(
            self.queue.engine, self.content, authorize=self.authorize, check_lease=self.queue.owns,
            open_verified=self._missing_context, recognize=self._missing_context,
            after_complete=self.queue.after_complete, open_for_task=self.open_for_task,
            recognize_for_task=self.recognize_for_task,
        )

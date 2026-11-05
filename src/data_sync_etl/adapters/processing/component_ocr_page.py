"""Lease-aware execution of one component-owned OCR page.

Dependencies are injected by trusted server code. This service is not yet
registered in the existing OCR worker and must not receive IDs or callbacks
directly from a client.
"""
from sqlalchemy import select

from data_sync_etl.adapters.persistence.component_ocr_outbox import (
    complete_verified_page,
    component_tables,
    require_isolated_prototype,
)
from data_sync_etl.domain.core import DomainError


class ComponentOCRPageExecutor:
    def __init__(self, component_engine, content, *, authorize, check_lease, open_verified, recognize,
                 after_complete=None, open_for_task=None, recognize_for_task=None):
        require_isolated_prototype(component_engine)
        self.component_engine = component_engine
        self.content = content
        self.authorize = authorize
        self.check_lease = check_lease
        self.open_verified = open_verified
        self.recognize = recognize
        self.after_complete = after_complete
        self.open_for_task = open_for_task
        self.recognize_for_task = recognize_for_task

    def execute_claimed(self, task):
        """Use a server-owned claimed task; never accept it from an API body."""
        return self.run(task['component_snapshot'], source_version_id=task['source_version_id'],
                        source_hash=task['source_hash'], fingerprint=task['processing_fingerprint'],
                        claim_token=task.get('claim_token'),
                        opener=(lambda: self.open_for_task(task)) if self.open_for_task else None,
                        recognizer=(lambda stream, page: self.recognize_for_task(task, stream, page))
                        if self.recognize_for_task else None)

    def run(self, snapshot, *, source_version_id, source_hash, fingerprint, claim_token=None,
            opener=None, recognizer=None):
        if not self.authorize(snapshot, source_version_id, source_hash):
            raise DomainError('COMPONENT_CONTENT_UNAUTHORIZED')
        with self.component_engine.begin() as connection:
            if not self.check_lease(connection, snapshot, claim_token):
                raise DomainError('COMPONENT_OCR_LEASE_LOST')
            jobs = component_tables['job']
            job = connection.execute(select(jobs).where(
                jobs.c.namespace == snapshot.get('namespace'),
                jobs.c.source_job_id == snapshot.get('source_job_id'),
                jobs.c.generation == snapshot.get('generation'),
            ).with_for_update()).mappings().one_or_none()
            if (job is None or job['tombstone'] or job['source_version_id'] != source_version_id
                    or job['source_hash'] != source_hash or job['processing_fingerprint'] != fingerprint
                    or job['master_version_id'] != snapshot.get('master_version_id')
                    or type(job['total_pages']) is not int or not 1 <= job['total_pages'] <= 2000
                    or type(snapshot.get('page_num')) is not int
                    or not 1 <= snapshot['page_num'] <= job['total_pages']):
                raise DomainError('COMPONENT_JOB_IDENTITY_MISMATCH')
            total_pages = job['total_pages']
        identity = dict(namespace=snapshot['namespace'], version_id=source_version_id,
                        generation=snapshot['generation'], page_num=snapshot['page_num'],
                        source_hash=source_hash, fingerprint=fingerprint)
        manifest = self.content.get_manifest(namespace=identity['namespace'], version_id=source_version_id,
                                             generation=identity['generation'])
        if manifest is None:
            self.content.put_manifest(namespace=identity['namespace'], version_id=source_version_id,
                                      generation=identity['generation'], total_pages=total_pages,
                                      source_hash=source_hash, fingerprint=fingerprint)
            manifest = self.content.get_manifest(namespace=identity['namespace'], version_id=source_version_id,
                                                 generation=identity['generation'])
            if manifest is None:
                raise DomainError('COMPONENT_MANIFEST_NOT_READY')
        if (manifest.get('source_hash') != source_hash or manifest.get('fingerprint') != fingerprint
                or manifest.get('total_pages') != total_pages
                or type(identity['page_num']) is not int or identity['page_num'] < 1
                or identity['page_num'] > manifest['total_pages']):
            raise DomainError('COMPONENT_MANIFEST_STALE')
        existing = self.content.get_page(**identity)
        reused = existing is not None
        if not reused:
            # The injected opener must resolve a trusted source and verify hash;
            # failure propagates, never becoming an empty OCR result.
            with (opener or self.open_verified)() as stream:
                result = (recognizer or self.recognize)(stream, snapshot['page_num'])
            self.content.put_page(**identity, raw_text=result.text,
                                  engine_name=result.engine_name, engine_version=result.engine_version,
                                  confidence=result.confidence, result_metadata=result.metadata)
        if not self.authorize(snapshot, source_version_id, source_hash):
            raise DomainError('COMPONENT_CONTENT_UNAUTHORIZED')
        with self.component_engine.begin() as connection:
            if not self.check_lease(connection, snapshot, claim_token):
                raise DomainError('COMPONENT_OCR_LEASE_LOST')
            if not complete_verified_page(connection, self.content, snapshot, source_version_id=source_version_id,
                                          source_hash=source_hash, fingerprint=fingerprint):
                raise DomainError('COMPONENT_OCR_STALE_REVISION')
            if self.after_complete is not None:
                self.after_complete(connection, snapshot, claim_token)
        return {'reused': reused, 'page_num': snapshot['page_num']}

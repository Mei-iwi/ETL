"""Lease-aware execution of one component-owned OCR page.

Dependencies are injected by trusted server code. This service is not yet
registered in the existing OCR worker and must not receive IDs or callbacks
directly from a client.
"""
from data_sync_etl.adapters.persistence.component_ocr_outbox import complete_verified_page
from data_sync_etl.domain.core import DomainError


class ComponentOCRPageExecutor:
    def __init__(self, component_engine, content, *, authorize, check_lease, open_verified, recognize):
        self.component_engine = component_engine
        self.content = content
        self.authorize = authorize
        self.check_lease = check_lease
        self.open_verified = open_verified
        self.recognize = recognize

    def run(self, snapshot, *, source_version_id, source_hash, fingerprint):
        if not self.authorize(snapshot, source_version_id, source_hash):
            raise DomainError('COMPONENT_CONTENT_UNAUTHORIZED')
        with self.component_engine.begin() as connection:
            if not self.check_lease(connection, snapshot):
                raise DomainError('COMPONENT_OCR_LEASE_LOST')
        identity = dict(namespace=snapshot['namespace'], version_id=source_version_id,
                        generation=snapshot['generation'], page_num=snapshot['page_num'],
                        source_hash=source_hash, fingerprint=fingerprint)
        existing = self.content.get_page(**identity)
        reused = existing is not None
        if not reused:
            # The injected opener must resolve a trusted source and verify hash;
            # failure propagates, never becoming an empty OCR result.
            with self.open_verified() as stream:
                result = self.recognize(stream, snapshot['page_num'])
            self.content.put_page(**identity, raw_text=result.text,
                                  engine_name=result.engine_name, engine_version=result.engine_version,
                                  confidence=result.confidence, result_metadata=result.metadata)
        if not self.authorize(snapshot, source_version_id, source_hash):
            raise DomainError('COMPONENT_CONTENT_UNAUTHORIZED')
        with self.component_engine.begin() as connection:
            if not self.check_lease(connection, snapshot):
                raise DomainError('COMPONENT_OCR_LEASE_LOST')
            complete_verified_page(connection, self.content, snapshot, source_version_id=source_version_id,
                                   source_hash=source_hash, fingerprint=fingerprint)
        return {'reused': reused, 'page_num': snapshot['page_num']}

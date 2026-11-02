"""Conservative reuse of existing SOURCE OCR pages in the five-collection topology."""
import re
from hashlib import sha256

from data_sync_etl.domain.core import DomainError


class SourceOCRProbe:
    """A server-owned proof provider must attest generation and page checksum.

    SOURCE's schema does not itself store an OCR-options fingerprint or a raw
    page checksum. Their absence is not evidence that an OCR page is valid.
    """

    def __init__(self, reader, *, authorize, proof_provider):
        if not callable(authorize) or not callable(proof_provider):
            raise ValueError('SOURCE_OCR_PROOF_CONFIG_REQUIRED')
        self.reader, self.authorize, self.proof_provider = reader, authorize, proof_provider

    def probe(self, snapshot, *, generation, processing_fingerprint):
        if (type(generation) is not int or generation < 1 or not isinstance(processing_fingerprint, str)
                or not re.fullmatch(r'[0-9a-f]{64}', processing_fingerprint)):
            raise DomainError('SOURCE_OCR_INVALID_IDENTITY')
        if not self.authorize(snapshot):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        job = snapshot.get('job')
        if job is None:
            # A page can survive a crash before SOURCE job state is committed.
            # Its ownership cannot be inferred solely from a reused version ID.
            orphaned = self.reader.ocr_pages(version_id=snapshot['source_version_id'], job_id=None,
                                              limit=1, offset=0)
            if not self.authorize(snapshot):
                raise DomainError('SOURCE_OCR_UNAUTHORIZED')
            return {'status': 'UNVERIFIABLE' if orphaned else 'ABSENT', 'missing_pages': ()}
        total = job.get('total_pages')
        if type(total) is not int or not 1 <= total <= 2000:
            orphaned = self.reader.ocr_pages(version_id=snapshot['source_version_id'], job_id=None,
                                              limit=1, offset=0)
            if not self.authorize(snapshot):
                raise DomainError('SOURCE_OCR_UNAUTHORIZED')
            return {'status': 'UNVERIFIABLE' if orphaned else 'NOT_READY', 'missing_pages': ()}
        tasks = snapshot.get('pages')
        if not isinstance(tasks, list):
            raise DomainError('SOURCE_OCR_INVALID_STATE')
        by_number = {task['page_num']: task for task in tasks}
        if len(by_number) != len(tasks) or any(type(number) is not int or not 1 <= number <= total for number in by_number):
            raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
        documents = []
        for offset in range(0, total + 1, 100):
            batch = self.reader.ocr_pages(version_id=snapshot['source_version_id'], job_id=job['id'],
                                          limit=100, offset=offset)
            documents.extend(batch)
            if len(batch) < 100:
                break
        if len(documents) > total:
            raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
        docs = {document.page_num: document for document in documents}
        if len(docs) != len(documents) or any(not 1 <= number <= total for number in docs):
            raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
        missing, unverifiable, recoverable = [], [], []
        for number in range(1, total + 1):
            task, document = by_number.get(number), docs.get(number)
            if document is None:
                missing.append(number)
                continue
            if (document.job_id != job['id'] or document.resource_version_id != snapshot['source_version_id']
                    or document.source_checksum.lower() != snapshot['source_hash']):
                raise DomainError('SOURCE_OCR_STALE')
            proof = self.proof_provider(snapshot, job, number)
            if (not isinstance(proof, dict) or proof.get('generation') != generation
                    or proof.get('processing_fingerprint') != processing_fingerprint
                    or 'engine_name' not in proof or 'engine_version' not in proof
                    or not isinstance(proof.get('raw_sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', proof['raw_sha256'])):
                unverifiable.append(number)
                continue
            if (document.projection_generation != generation
                    or document.engine_name != proof['engine_name']
                    or document.engine_version != proof['engine_version']
                    or sha256(document.raw_text.encode('utf-8')).hexdigest() != proof['raw_sha256']):
                raise DomainError('SOURCE_OCR_STALE')
            # PostgreSQLSourceOCRReader maps SOURCE COMPLETED to the shared
            # page status SUCCEEDED before returning its snapshot.
            if task is None or task.get('status') != 'SUCCEEDED':
                # Mongo may have committed before SOURCE PG; recover only after
                # a source-authorized state transition, never count it complete.
                recoverable.append(number)
        if not self.authorize(snapshot):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        if unverifiable:
            return {'status': 'UNVERIFIABLE', 'missing_pages': tuple(missing),
                    'unverifiable_pages': tuple(unverifiable), 'recoverable_pages': tuple(recoverable)}
        if missing or recoverable:
            return {'status': 'PARTIAL', 'missing_pages': tuple(missing),
                    'recoverable_pages': tuple(recoverable)}
        if job.get('status') != 'COMPLETED' or job.get('completed_pages') != total or job.get('failed_pages') != 0:
            return {'status': 'NOT_READY', 'missing_pages': ()}
        return {'status': 'COMPLETE_VERIFIED', 'missing_pages': ()}

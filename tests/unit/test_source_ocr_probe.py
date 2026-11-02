from hashlib import sha256
from types import SimpleNamespace

import pytest

from data_sync_etl.application.source_ocr_probe import SourceOCRProbe
from data_sync_etl.domain.core import DomainError


def snapshot():
    return dict(namespace='source-a', source_version_id='v1', master_version_id='m1', source_hash='a' * 64,
                job=dict(id='j1', status='COMPLETED', total_pages=2, completed_pages=2, failed_pages=0),
                pages=[dict(page_num=1, status='SUCCEEDED'), dict(page_num=2, status='SUCCEEDED')])


def document(number, raw):
    return SimpleNamespace(job_id='j1', resource_version_id='v1', page_num=number, raw_text=raw,
                           source_checksum='a' * 64, projection_generation=1,
                           engine_name='engine', engine_version='1')


def test_verified_empty_page_is_reused_only_with_independent_proof():
    docs = [document(1, ''), document(2, 'nội dung')]
    reader = SimpleNamespace(ocr_pages=lambda **kwargs: docs[kwargs['offset']:kwargs['offset'] + kwargs['limit']])
    def proof(_snapshot, _job, number):
        return dict(generation=1, processing_fingerprint='b' * 64, engine_name='engine', engine_version='1',
                    raw_sha256=sha256(docs[number - 1].raw_text.encode()).hexdigest())
    probe = SourceOCRProbe(reader, authorize=lambda _: True, proof_provider=proof)
    assert probe.probe(snapshot(), generation=1, processing_fingerprint='b' * 64)['status'] == 'COMPLETE_VERIFIED'
    unproven = SourceOCRProbe(reader, authorize=lambda _: True, proof_provider=lambda *_: None)
    assert unproven.probe(snapshot(), generation=1, processing_fingerprint='b' * 64)['status'] == 'UNVERIFIABLE'


def test_missing_or_uncommitted_pages_do_not_become_verified():
    docs = [document(1, '')]
    reader = SimpleNamespace(ocr_pages=lambda **kwargs: docs)
    def proof(*_):
        return dict(generation=1, processing_fingerprint='b' * 64, engine_name='engine', engine_version='1',
                    raw_sha256=sha256(b'').hexdigest())
    probe = SourceOCRProbe(reader, authorize=lambda _: True, proof_provider=proof)
    assert probe.probe(snapshot(), generation=1, processing_fingerprint='b' * 64) == {
        'status': 'PARTIAL', 'missing_pages': (2,), 'recoverable_pages': ()
    }
    current = snapshot()
    current['pages'][0]['status'] = 'PENDING'
    result = probe.probe(current, generation=1, processing_fingerprint='b' * 64)
    assert result['missing_pages'] == (2,)
    assert result['recoverable_pages'] == (1,)


def test_stale_or_revoked_source_never_returns_success():
    docs = [document(1, ''), document(2, 'nội dung')]
    reader = SimpleNamespace(ocr_pages=lambda **kwargs: docs)
    def proof(*_):
        return dict(generation=1, processing_fingerprint='b' * 64, engine_name='engine', engine_version='1',
                    raw_sha256='c' * 64)
    probe = SourceOCRProbe(reader, authorize=lambda _: True, proof_provider=proof)
    with pytest.raises(DomainError, match='SOURCE_OCR_STALE'):
        probe.probe(snapshot(), generation=1, processing_fingerprint='b' * 64)
    authorization = iter([True, False])
    def valid_proof(_snapshot, _job, number):
        return dict(generation=1, processing_fingerprint='b' * 64, engine_name='engine', engine_version='1',
                    raw_sha256=sha256(docs[number - 1].raw_text.encode()).hexdigest())
    revoked = SourceOCRProbe(reader, authorize=lambda _: next(authorization), proof_provider=valid_proof)
    with pytest.raises(DomainError, match='SOURCE_OCR_UNAUTHORIZED'):
        revoked.probe(snapshot(), generation=1, processing_fingerprint='b' * 64)


def test_absent_job_with_orphan_mongo_page_is_not_treated_as_empty():
    current = snapshot()
    current['job'] = None
    current['pages'] = []
    reader = SimpleNamespace(ocr_pages=lambda **_: [document(1, '')])
    probe = SourceOCRProbe(reader, authorize=lambda _: True, proof_provider=lambda *_: None)
    assert probe.probe(current, generation=1, processing_fingerprint='b' * 64)['status'] == 'UNVERIFIABLE'
    reader.ocr_pages = lambda **_: []
    assert probe.probe(current, generation=1, processing_fingerprint='b' * 64)['status'] == 'ABSENT'

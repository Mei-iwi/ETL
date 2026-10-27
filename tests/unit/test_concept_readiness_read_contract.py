"""P01: operator contract proof validation, with no real credentials or DB."""
from dataclasses import replace

import pytest

from data_sync_etl.adapters.concept_corpus import (
    CorpusQueryPlanGate,
    CorpusReadOnlyAccessGate,
    ReadOnlyMongoEvidenceReader,
)
from data_sync_etl.domain.concept_read_evidence import (
    READ_PLANS,
    CorpusReadAttestation,
    ReadEvidenceGate,
    secret_reference,
)
from data_sync_etl.ports.concept_corpus import ConceptBlocked


def attestation(path):
    return CorpusReadAttestation('test-principal-reference', 'fixture', secret_reference(path), 'test-audit-only',
        90, 200, True, tuple(READ_PLANS.items()), credential_fingerprint='0' * 64)


def test_real_factory_blocks_missing_typed_evidence_before_secret(tmp_path):
    with pytest.raises(ConceptBlocked, match='ATTESTATION_MISSING'):
        ReadOnlyMongoEvidenceReader.from_secret_reference(tmp_path / 'not-read', 'fixture',
            CorpusReadOnlyAccessGate(lambda: True), query_gate=CorpusQueryPlanGate(lambda _: True))


@pytest.mark.parametrize('alter,code', [({'expires_at': 99}, 'EXPIRED'), ({'issued_at': 101}, 'EXPIRED'),
    ({'database': 'other'}, 'SCOPE_MISMATCH'), ({'secret_reference_hash': 'other'}, 'SCOPE_MISMATCH'),
    ({'plans': (('learning_resources', 'unindexed'),)}, 'PLAN_MISMATCH'), ({'read_only': 'true'}, 'UNTRUSTED')])
def test_read_evidence_scope_expiry_plan_and_trust(tmp_path, alter, code):
    path = tmp_path / 'never-opened'
    proof = replace(attestation(path), **alter)
    with pytest.raises(ConceptBlocked, match=code):
        ReadEvidenceGate(proof, verifier=lambda _: True, clock=lambda: 100).require('fixture', path)
    assert not path.exists()


def test_bound_evidence_requires_trusted_provider_not_payload(tmp_path):
    path = tmp_path / 'reference-only'
    proof = attestation(path)
    with pytest.raises(ConceptBlocked, match='MISSING'):
        ReadEvidenceGate(proof).require('fixture', path)
    ReadEvidenceGate(proof, verifier=lambda _: True, clock=lambda: 100).require('fixture', path)


def test_source_outage_is_sanitized():
    def unavailable(*args, **kwargs):
        raise OSError('sensitive endpoint must never escape')
    reader = ReadOnlyMongoEvidenceReader({'learning_resources': unavailable}, CorpusReadOnlyAccessGate(lambda: True),
        query_gate=CorpusQueryPlanGate(lambda _: True))
    with pytest.raises(ConceptBlocked, match='^CORPUS_SOURCE_UNAVAILABLE$'):
        reader.metadata('approved-metadata-id')


def test_replaced_credential_is_rejected_before_client_creation(tmp_path):
    path = tmp_path / 'TEST_ONLY-reference'
    path.write_text('mongodb://fixture.invalid', encoding='utf-8')
    gate = ReadEvidenceGate(attestation(path), verifier=lambda _: True, clock=lambda: 100)
    def forbidden(*args, **kwargs):
        pytest.fail('Changed credential reached client construction')
    with pytest.raises(ConceptBlocked, match='CREDENTIAL_CHANGED'):
        ReadOnlyMongoEvidenceReader.from_secret_reference(path, 'fixture', CorpusReadOnlyAccessGate(lambda: True),
            query_gate=CorpusQueryPlanGate(lambda _: True), read_evidence=gate, client_factory=forbidden)


def test_reader_rechecks_attestation_expiry_before_cursor(tmp_path):
    from hashlib import sha256
    path = tmp_path / 'TEST_ONLY-reference'
    uri = 'mongodb://fixture.invalid'
    path.write_text(uri, encoding='utf-8')
    now = [100]
    evidence = ReadEvidenceGate(replace(attestation(path), credential_fingerprint=sha256(uri.encode()).hexdigest()),
        verifier=lambda _: True, clock=lambda: now[0])
    class Collection:
        def find(self, *args, **kwargs):
            pytest.fail('Expired operator proof reached a cursor')
    class Client:
        def get_database(self, *args, **kwargs):
            return {'learning_resources': Collection(), 'mongo_content_units': Collection()}
        def close(self):
            pass
    reader = ReadOnlyMongoEvidenceReader.from_secret_reference(path, 'fixture', CorpusReadOnlyAccessGate(lambda: True),
        query_gate=CorpusQueryPlanGate(lambda _: True), read_evidence=evidence, client_factory=lambda *a, **k: Client())
    try:
        now[0] = 201
        with pytest.raises(ConceptBlocked, match='ATTESTATION_EXPIRED'):
            reader.metadata('approved-metadata-id')
    finally:
        reader.close()

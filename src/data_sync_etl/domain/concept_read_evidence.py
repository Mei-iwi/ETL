"""Operator-attested read contract. No credential contents, DB probes or env IO."""
import hmac
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from time import time

from data_sync_etl.domain.indexing import digest
from data_sync_etl.ports.concept_corpus import ConceptBlocked

READ_PLANS = {'learning_resources': 'metadata-id-equality-v1',
    'mongo_content_units': 'approved-unit-ids-version-current-v1'}


def secret_reference(path):
    # Identity of the external reference, never its credential contents.
    return digest(str(Path(path).absolute()))


@dataclass(frozen=True)
class CorpusReadAttestation:
    principal_reference: str
    database: str
    secret_reference_hash: str
    audit_reference: str
    issued_at: float
    expires_at: float
    read_only: bool
    plans: tuple[tuple[str, str], ...]
    credential_fingerprint: str = ''


class ReadEvidenceGate:
    """Verifier must be installed from an operator trust channel, never HTTP JSON.

    A serialized evidence document is not trusted by itself. Composition installs
    no verifier. Boolean compatibility gates remain usable for injected fakes.
    """
    def __init__(self, evidence=None, *, verifier=None, clock=time):
        self.evidence, self.verifier, self.clock = evidence, verifier, clock

    def require(self, database, secret_file):
        proof = self.evidence
        if not isinstance(proof, CorpusReadAttestation) or self.verifier is None:
            raise ConceptBlocked('CORPUS_ATTESTATION_MISSING')
        try:
            trusted = self.verifier(proof) is True
        except Exception:
            trusted = False
        if (not trusted or proof.read_only is not True
                or any(not isinstance(value, str) or not value or len(value) > 256
                    for value in (proof.audit_reference, proof.principal_reference, proof.database))
                or not isinstance(proof.credential_fingerprint, str) or len(proof.credential_fingerprint) != 64
                or any(c not in '0123456789abcdef' for c in proof.credential_fingerprint)):
            raise ConceptBlocked('CORPUS_ATTESTATION_UNTRUSTED')
        now = self.clock()
        if (any(not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not isfinite(stamp)
                for stamp in (proof.issued_at, proof.expires_at)) or not proof.issued_at <= now < proof.expires_at):
            raise ConceptBlocked('CORPUS_ATTESTATION_EXPIRED')
        if proof.database != database or proof.secret_reference_hash != secret_reference(secret_file):
            raise ConceptBlocked('CORPUS_ATTESTATION_SCOPE_MISMATCH')
        try:
            valid_plans = dict(proof.plans) == READ_PLANS and len(proof.plans) == len(READ_PLANS)
        except (ValueError, TypeError):
            valid_plans = False
        if not valid_plans:
            raise ConceptBlocked('CORPUS_ATTESTATION_PLAN_MISMATCH')

    def verify_secret(self, uri):
        actual = sha256(uri.encode('utf-8')).hexdigest()
        if not hmac.compare_digest(actual, self.evidence.credential_fingerprint):
            raise ConceptBlocked('CORPUS_CREDENTIAL_CHANGED')

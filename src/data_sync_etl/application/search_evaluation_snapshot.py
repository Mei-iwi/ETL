"""Full lexical evaluation contract from an already verified test export.

Pure data functions: never connect to DB, infer grants, or open runtime access.
An eligibility declaration is provenance, NOT authorization. A trusted exporter
must enforce SearchAccess and both snapshot phases before supplying these rows.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.domain.concepts import EXPANSION_VERSION, Dictionary, ExpansionPolicy, Snapshot
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import (
    ANALYZER_VERSION,
    RANKING_VERSION,
    REPRESENTATION_VERSION,
    LexicalDocument,
    bm25,
)


class StrictSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class EvaluationBag(StrictSnapshot):
    bag_id: str = Field(min_length=1, max_length=30)
    version_id: str = Field(min_length=1, max_length=30)
    resource_id: str = Field(min_length=1, max_length=30)
    generation: int = Field(ge=0)
    content_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    eligibility: Literal['verified_authorized_snapshot']
    lexical: LexicalDocument


class SearchEvaluationSnapshot(StrictSnapshot):
    kind: Literal['full-lexical-search-snapshot-v1']
    provenance: Literal['isolated_fixture', 'approved_test_export']
    dictionary: Snapshot
    dictionary_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    policy: ExpansionPolicy
    ranking_version: str
    expansion_version: str
    analyzer_version: str
    representation_version: str
    bm25_k1: float = 1.2
    bm25_b: float = .75
    min_coverage: float = Field(default=0., ge=0, le=1, allow_inf_nan=False)
    bags: list[EvaluationBag] = Field(min_length=1, max_length=10000)
    checksum: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def integrity(self):
        if (self.dictionary_fingerprint != self.dictionary.fingerprint
                or self.ranking_version != RANKING_VERSION or self.expansion_version != EXPANSION_VERSION
                or self.analyzer_version != ANALYZER_VERSION
                or self.representation_version != REPRESENTATION_VERSION
                or self.bm25_k1 != 1.2 or self.bm25_b != .75
                or len({b.bag_id for b in self.bags}) != len(self.bags)):
            raise ValueError('Unsupported or inconsistent lexical snapshot')
        versions = {}
        for bag in self.bags:
            identity = (bag.resource_id, bag.generation, bag.content_fingerprint)
            if versions.setdefault(bag.version_id, identity) != identity:
                raise ValueError('Mixed version generation/fingerprint')
            if bag.lexical.analyzer != ANALYZER_VERSION or bag.lexical.version != REPRESENTATION_VERSION:
                raise ValueError('Lexical contract mismatch')
        if digest(self.model_dump(exclude={'checksum'})) != self.checksum:
            raise ValueError('Lexical snapshot checksum mismatch')
        return self


def capture_lexical_snapshot(bags, dictionary, policy, *, provenance, min_coverage=0.):
    """Caller supplies only bags from one fully authorized, verified corpus.

    Full TF/title/passages/nonhits are retained so DF/avgdl are reproducible.
    This private artifact contains source-derived terms and must not be public.
    """
    payload = dict(kind='full-lexical-search-snapshot-v1', provenance=provenance,
        dictionary=dictionary.snapshot.model_dump(), dictionary_fingerprint=dictionary.fingerprint,
        policy=policy.model_dump(), ranking_version=RANKING_VERSION, analyzer_version=ANALYZER_VERSION,
        expansion_version=EXPANSION_VERSION,
        representation_version=REPRESENTATION_VERSION, bm25_k1=1.2, bm25_b=.75,
        min_coverage=min_coverage, bags=[row.model_dump() for row in bags])
    return SearchEvaluationSnapshot.model_validate({**payload, 'checksum': digest(payload)})


def replay_lexical_snapshot(query, snapshot, *, domain=None):
    return bm25(query, {b.bag_id: b.lexical for b in snapshot.bags},
        dictionary=Dictionary(snapshot.dictionary), policy=snapshot.policy, domain=domain,
        k1=snapshot.bm25_k1, b=snapshot.bm25_b, min_coverage=snapshot.min_coverage)

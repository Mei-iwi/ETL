"""Select real, already-authorized Chunk evidence within ONE winning version.

Retrieval admission/ranking and evidence selection are separate. These scores
never establish answerability or grant access. Callers own freshness/ACL checks.
"""
import math
from dataclasses import dataclass

from data_sync_etl.domain.context_ranking import ContextRanker
from data_sync_etl.domain.search import SearchError


@dataclass(frozen=True)
class EvidenceCandidate:
    version_id: str
    bag_id: str
    chunk: dict
    dense_score: float


def bounded_evidence(candidates, *, version_id, top_n=3):
    if type(top_n) is not int or not 1 <= top_n <= 10:
        raise ValueError('Evidence budget must be 1..10')
    unique = {}
    for candidate in candidates:
        if (candidate.version_id != version_id or not candidate.bag_id
                or not isinstance(candidate.chunk.get('id'), str)
                or not candidate.chunk.get('content_unit_id')
                or not isinstance(candidate.chunk.get('text'), str)
                or type(candidate.dense_score) not in (int, float)
                or not math.isfinite(candidate.dense_score) or not -1 <= candidate.dense_score <= 1):
            raise SearchError('SEARCH_INDEX_STALE', 409)
        key = candidate.chunk['id']
        if key in unique and unique[key] != candidate:
            raise SearchError('SEARCH_INDEX_STALE', 409)
        unique[key] = candidate
    return sorted(unique.values(), key=lambda c: (-c.dense_score, c.chunk['id']))[:top_n]


def select_evidence(candidates, *, version_id, strategy='dense', top_n=3, margin=.08,
                    query='', keywords=(), dictionary=None, expansion=None, policy=None,
                    checkpoint=lambda: None):
    if strategy not in ('dense', 'coverage', 'context') or not math.isfinite(margin) or not 0 <= margin <= .15:
        raise ValueError('Unsupported evidence policy')
    ordered = bounded_evidence(candidates, version_id=version_id, top_n=top_n)
    if not ordered:
        return None
    if strategy == 'dense':
        return ordered[0]
    # Preserve strong semantic evidence; lexical/context scoring may only choose
    # among a bounded near-tie set, never promote an arbitrarily distant Chunk.
    ordered = [c for c in ordered if ordered[0].dense_score - c.dense_score <= margin]
    ranker = ContextRanker(query, keywords, [c.chunk['text'] for c in ordered],
        checkpoint=checkpoint, dictionary=dictionary, expansion=expansion, policy=policy)
    checkpoint()
    scores = [ranker.score(i) for i in range(len(ordered))]
    # coverage is a separate ablation; context uses the existing phrase/concept
    # lexicographic policy. Raw dense and ID break ties deterministically.
    return max(enumerate(ordered), key=lambda pair: (
        (scores[pair[0]][2:] if strategy == 'coverage' else scores[pair[0]]),
        pair[1].dense_score, -pair[0]))[1]

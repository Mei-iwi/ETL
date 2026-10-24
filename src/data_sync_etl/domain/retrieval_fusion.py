"""Opt-in RRF over a common ResourceVersion identity, independent of retrievers.

Inputs MUST already be authorized and freshness-verified. Keys can represent
ContentBags or Chunks; callers supply their exact version mapping. No text,
cross-request state, permission decisions, or score-as-confidence semantics.
"""
from dataclasses import dataclass
from math import isfinite

from data_sync_etl.domain.search import ranks, rrf


@dataclass(frozen=True)
class VersionFusion:
    scores: dict[str, float]
    components: dict[str, dict[str, float]]
    representatives: dict[str, str]


def version_rrf(branches: dict[str, dict[str, float]], versions: dict[str, str], *, weights=None) -> VersionFusion:
    reduced, winners = {}, {}
    for name, branch in branches.items():
        best = {}
        for key in sorted(branch, key=lambda key: (-branch[key], key)):
            best.setdefault(versions[key], key)
        winners[name] = best
        reduced[name] = {version: branch[key] for version, key in best.items()}
    if weights is None:
        fused = rrf(*reduced.values())
    else:
        if (set(weights) != set(branches) or any(type(v) not in (int, float)
                or not isfinite(v) or not 0 < v <= 4 for v in weights.values())):
            raise ValueError('Finite positive weight per branch required')
        fused = {}
        for name, branch in reduced.items():
            for version, rank in ranks(branch).items():
                fused[version] = fused.get(version, 0.) + weights[name] / (60 + rank)
    components = {version: {name: branch[version] for name, branch in reduced.items() if version in branch}
                  for version in fused}
    # Dense evidence retains the actual winning passage; lexical-only versions
    # use their lexical winner. Selection never crosses a version boundary.
    representatives = {}
    for name in [*sorted(name for name in branches if name != 'vector'), 'vector']:
        representatives.update(winners.get(name, {}))
    all_keys = set().union(*(set(branch) for branch in branches.values()))
    return VersionFusion({key: fused[versions[key]] for key in all_keys},
                         components, representatives)


def version_budget(branch, versions, limit):
    """Bound a branch AFTER version diversity; never truncate BM25 statistics."""
    if type(limit) is not int or limit < 1:
        raise ValueError('Positive version budget required')
    result, seen = {}, set()
    for key in sorted(branch, key=lambda key: (-branch[key], key)):
        version = versions[key]
        if version in seen:
            continue
        result[key] = branch[key]
        seen.add(version)
        if len(result) == limit:
            break
    return result


def query_form(query):
    """Observable form only, not inferred language, relevance or confidence."""
    import re

    from data_sync_etl.domain.lexical import surface_tokens

    text = query.strip()
    if re.fullmatch(r'[A-Z0-9]{2,}(?:[_-][A-Z0-9]+)+', text):
        return 'identifier'
    if text.casefold() in ('c++', 'c#', '.net'):
        return 'technical'
    if re.fullmatch(r'[A-Z][A-Z0-9]{1,7}', text):
        return 'acronym'
    if '?' in text or text.casefold().startswith(('how ', 'why ', 'what ', 'comment ', 'pourquoi ',
                                                 'làm sao', 'tại sao', 'cách ', 'giải thích')):
        return 'question'
    return 'short' if len(surface_tokens(text)) <= 4 else 'descriptive'


def vector_priority_rrf(query, branches, versions):
    """Research arm: protect lexical identifiers/symbols/acronyms with equal RRF.

    Other query forms retain dense order and append lexical-only versions.
    No automatic deployment; this is a deliberately testable, imperfect policy.
    """
    if query_form(query) in ('identifier', 'technical', 'acronym'):
        return version_rrf(branches, versions)
    result = version_rrf(branches, versions)
    dense = version_budget(branches.get('vector', {}), versions, max(1, len(versions)))
    lexical = version_budget(branches.get('bow', {}), versions, max(1, len(versions)))
    order = list(dict.fromkeys([*[versions[k] for k in dense], *[versions[k] for k in lexical]]))
    scores = {version: 1. / (60 + rank) for rank, version in enumerate(order, 1)}
    return VersionFusion({key: scores[versions[key]] for key in result.scores},
                         result.components, result.representatives)

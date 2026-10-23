"""Versioned inputs and vector validation, independent of BoW and storage."""
import math

from data_sync_etl.domain.indexing import IndexingError, digest, validate_vector

CONTEXT_FORMAT = 'verified-headings-v1'


def normalize_dense(vector, dimension=1024):
    values = validate_vector(vector, dimension)
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm <= 0:
        raise IndexingError('INVALID_EMBEDDING', 503)
    return [value / norm for value in values]


def embedding_input(chunk, *, title=None, headings=(), subjects=(), grades=(), strategy='contextual'):
    """Never mutate raw text, add technical identifiers, or synthesize metadata.

    Metadata is bounded separately; tokenizer overflow fails explicitly in the
    adapter. Raw text is NEVER silently truncated to make a request succeed.
    """
    text = chunk['text']
    if not isinstance(text, str) or not text.strip():
        raise IndexingError('SEMANTIC_EMPTY_CHUNK', 422)
    if strategy == 'raw':
        return text
    if strategy != 'contextual':
        raise IndexingError('SEMANTIC_STRATEGY_INVALID', 422)
    def clean(value):
        return ' '.join(value.split())[:240] if isinstance(value, str) else ''
    metadata = []
    if clean(title):
        metadata.append('Title: ' + clean(title))
    for label, values in [('Subject', subjects), ('Grade', grades)]:
        names = sorted({clean(value) for value in values if clean(value)})
        if names:
            metadata.append(label + ': ' + '; '.join(names)[:240])
    path = [clean(item) for item in headings if clean(item)]
    if path:
        metadata.append('Heading: ' + ' > '.join(path)[-720:])
    return '\n'.join([*metadata, 'Content: ' + text])


def context_headings(units, unit_id):
    nodes = {unit['id']: unit for unit in units}
    path, seen = [], set()
    while unit_id is not None:
        if unit_id in seen or unit_id not in nodes:
            raise IndexingError('BAG_STRUCTURE_INVALID')
        seen.add(unit_id)
        unit = nodes[unit_id]
        if unit.get('title'):
            path.append(unit['title'])
        unit_id = unit['parent_unit_id']
    return list(reversed(path))


def record_identity(index_id, chunk_id):
    return digest([index_id, chunk_id])


def diverse_candidates(scores, version_by_key, limit):
    """One best hit per version before a resource-oriented rerank budget."""
    if limit < 1:
        raise ValueError('Positive candidate limit required')
    seen, result = set(), []
    for key in sorted(scores, key=lambda key: (-scores[key], key)):
        version = version_by_key[key]
        if version in seen:
            continue
        seen.add(version)
        result.append(key)
        if len(result) == limit:
            break
    return result

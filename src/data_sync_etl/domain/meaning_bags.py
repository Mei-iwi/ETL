"""Model-derived groups of observed source expressions; no synonym dictionary.

Groups are local to a ContentBag, not universal ontology IDs. Complete-link
admission prevents a chain of related sentences becoming one alleged meaning.
Every member retains its own vector and exact source span for retrieval.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.domain.context_ranking import ContextRanker
from data_sync_etl.domain.indexing import IndexingError, digest, validate_vector, word_windows
from data_sync_etl.domain.search import cosine, cosine_many

VERSION = 'observed-multilingual-meanings-v1'
SENTENCE_END = re.compile(r'[。！？]+|[.!?]+(?:\s+|$)|\n+')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class MeaningSource(StrictModel):
    unit_id: str = Field(min_length=1, max_length=30)
    kind: Literal['title', 'text']
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def offsets(self):
        if self.end - self.start != len(self.text):
            raise ValueError('Meaning source offset mismatch')
        return self


class MeaningMember(MeaningSource):
    embedding: list[float]


class MeaningGroup(StrictModel):
    id: str = Field(pattern=r'^[0-9a-f]{24}$')
    members: list[int] = Field(min_length=1, max_length=256)


class MeaningBag(StrictModel):
    version: Literal['observed-multilingual-meanings-v1']
    model: str = Field(min_length=1, max_length=255)
    dimension: int = Field(ge=1, le=4096)
    group_threshold: float = Field(gt=0, le=1, allow_inf_nan=False)
    source_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    members: list[MeaningMember] = Field(min_length=1, max_length=256)
    groups: list[MeaningGroup] = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def consistent(self):
        for member in self.members:
            validate_vector(member.embedding, self.dimension)
        if sorted(index for group in self.groups for index in group.members) != list(range(len(self.members))):
            raise ValueError('Meaning groups must partition source members')
        if any(group.id != digest([self.members[i].model_dump(exclude={'embedding'})
                                   for i in group.members])[:24] for group in self.groups):
            raise ValueError('Meaning group source identity mismatch')
        payload = self.model_dump(exclude={'fingerprint'})
        if self.fingerprint != digest(payload):
            raise ValueError('Meaning bag checksum mismatch')
        return self


def sources(units, root_id, title):
    """Keep complete source coverage, splitting long sentences without truncation.

    Titles/line-separated terms and descriptions are both observed expressions.
    Bounded character windows also handle languages without whitespace. Word
    windows avoid cutting words where possible; all offsets refer to raw text.
    """
    result = []
    if title and title.strip():
        result.append(MeaningSource(unit_id=root_id, kind='title', start=0, end=len(title), text=title))
    for unit_id, text in units:
        start = 0
        boundaries = [match.end() for match in SENTENCE_END.finditer(text)] + [len(text)]
        for end in boundaries:
            segment = text[start:end]
            left = start + len(segment) - len(segment.lstrip())
            right = end - len(segment) + len(segment.rstrip())
            if right > left:
                for offset in range(left, right, 1600):
                    stop = min(right, offset + 1600)
                    for begin, finish, value in word_windows(text[offset:stop], 128, 0):
                        result.append(MeaningSource(unit_id=unit_id, kind='text',
                                                    start=offset + begin, end=offset + finish, text=value))
            start = end
    if not result or len(result) > 256:
        raise IndexingError('MEANING_BAG_LIMIT_EXCEEDED', 422)
    return result


def cluster(members, threshold):
    groups = []
    for ordinal, member in enumerate(members):
        eligible = []
        for index, indices in enumerate(groups):
            similarity = min(cosine(member.embedding, members[i].embedding) for i in indices)
            if similarity >= threshold:
                eligible.append((similarity, -index, index))
        if eligible:
            groups[max(eligible)[2]].append(ordinal)
        else:
            groups.append([ordinal])
    return [MeaningGroup(id=digest([members[i].model_dump(exclude={'embedding'}) for i in indices])[:24],
                         members=indices) for indices in groups]


def source_fingerprint(observed, model, dimension, threshold):
    return digest([VERSION, [source.model_dump() for source in observed], model, dimension, threshold])


def build_meanings(units, root_id, title, model, threshold):
    observed = sources(units, root_id, title)
    vectors, members = {}, []
    unique = list(dict.fromkeys(source.text for source in observed))
    encode_many = getattr(model, 'embed_many', None)
    for start in range(0, len(unique), 16):
        batch = unique[start:start + 16]
        encoded = encode_many(batch) if encode_many is not None else [model.embed(text) for text in batch]
        if len(encoded) != len(batch):
            raise IndexingError('INVALID_EMBEDDING', 503)
        vectors.update({text: validate_vector(vector, model.dimension)
                        for text, vector in zip(batch, encoded, strict=True)})
    for source in observed:
        # Repeated occurrences preserve provenance but share one encoding per bag.
        members.append(MeaningMember(**source.model_dump(), embedding=vectors[source.text]))
    identity = f'{model.model_id}@{model.version}'
    payload = dict(version=VERSION, model=identity, dimension=model.dimension,
                   group_threshold=threshold,
                   source_fingerprint=source_fingerprint(observed, identity, model.dimension, threshold),
                   members=[member.model_dump() for member in members],
                   groups=[group.model_dump() for group in cluster(members, threshold)])
    return MeaningBag(**payload, fingerprint=digest(payload))


def verify_meanings(bag, units, root_id, title, model, dimension, threshold):
    observed = sources(units, root_id, title)
    return (bag is not None and bag.version == VERSION and bag.model == model
            and bag.dimension == dimension and bag.group_threshold == threshold
            and bag.source_fingerprint == source_fingerprint(observed, model, dimension, threshold)
            and [MeaningSource(**member.model_dump(exclude={'embedding'})) for member in bag.members] == observed)


def match_meaning(query_vector, bag, *, context=None):
    """Score actual observed variants, never an averaged vector of mixed topics."""
    scores = cosine_many(query_vector, [member.embedding for member in bag.members])
    index = max(range(len(bag.members)), key=lambda i: (scores[i], -i))
    retrieval_score = scores[index]
    if context is not None and context.get('margin', .08) > 0:
        # Only disambiguate near-equal dense matches. Never let arbitrary exact
        # words promote a semantically distant span or cross a source boundary.
        pool = [i for i, score in enumerate(scores)
                if scores[index] - score <= context.get('margin', .08)]
        if len(pool) > 1:
            ranker = ContextRanker(context['query'], context['keywords'],
                [bag.members[i].text for i in pool], expansion=context['expansion'],
                dictionary=context['dictionary'], policy=context['policy'],
                expand_concepts=context['policy'].enabled)
            supported = [(ranker.score(ordinal), scores[i], -i, i) for ordinal, i in enumerate(pool)]
            best = max(supported)
            if any(best[0]):
                index = best[-1]
    member = bag.members[index]
    group = next(group for group in bag.groups if index in group.members)
    variants = list(dict.fromkeys(bag.members[i].text for i in group.members))
    # Retrieval is scored at ContentBag level. Choosing its explanatory span
    # must not lower its admission/rank or turn a valid result into an empty hit.
    return retrieval_score, member, variants

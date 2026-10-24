"""Conservative missing-accent variants from approved Vietnamese phrases only.

No corpus reads, global ASCII index, guessed diacritics or single-word synonyms.
The original query always remains a separate retrieval branch.
"""
import re
import unicodedata
from functools import cached_property

VERSION = 'approved-missing-accents-v1'
WORDS = re.compile(r'[^\W\d_]+', re.UNICODE)


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold().replace('đ', 'd'))
                   if unicodedata.category(c) != 'Mn')


class AccentAliases:
    def __init__(self, dictionary):
        self.dictionary = dictionary

    @cached_property
    def phrases(self):
        result = {}
        for terms in self.dictionary.aliases.values():
            for term in terms:
                if (term.language == 'vi' and term.kind != 'abbreviation'
                        and len(term.normalized.split()) >= 2
                        and all(WORDS.fullmatch(w) for w in term.normalized.split())):
                    result.setdefault(fold(term.normalized), []).append(term)
        return result


def approved_accent_variant(query, dictionary, *, policy, domain=None):
    if not policy.enabled:
        return None
    # Dictionary instances are immutable runtime snapshots; this derived index
    # contains no query, content or authorization data and is loaded once.
    if not hasattr(dictionary, '_accent_aliases'):
        dictionary._accent_aliases = AccentAliases(dictionary)
    canonical = unicodedata.normalize('NFC', query)
    words = list(WORDS.finditer(canonical))
    replacements = []
    for phrase, terms in dictionary._accent_aliases.phrases.items():
        candidates = [t for t in terms if t.confidence >= policy.min_confidence
                      and (domain is None or t.domain == domain)]
        # Fold collisions cannot be resolved by selecting the first alias.
        surfaces = {(t.concept_id, t.normalized) for t in candidates}
        if len(surfaces) != 1:
            continue
        restored = candidates[0].normalized
        if fold(restored) == restored:
            continue
        width = len(phrase.split())
        for start in range(len(words) - width + 1):
            selected = words[start:start + width]
            # Whitespace is allowed inside a phrase; punctuation, identifiers
            # and technical syntax must never be crossed or rewritten.
            if any(not canonical[a.end():b.start()].isspace() for a, b in zip(selected, selected[1:], strict=False)):
                continue
            observed = ' '.join(m.group().casefold() for m in selected)
            if fold(observed) != phrase or observed == restored:
                continue
            if len(observed) != len(restored) or any(
                    fold(a) != a and a != b for a, b in zip(observed, restored, strict=True)):
                continue  # Preserve any diacritics the user DID supply.
            before = canonical[selected[0].start()-1:selected[0].start()] if selected[0].start() else ''
            after = canonical[selected[-1].end():selected[-1].end()+1]
            if before == '_' or after == '_' or before.isdigit() or after.isdigit():
                continue
            replacements.append((selected[0].start(), selected[-1].end(), restored))
    accepted, occupied = [], set()
    for start, end, value in sorted(replacements, key=lambda r: (-(r[1]-r[0]), r[0], r[2])):
        if not occupied.intersection(range(start, end)):
            accepted.append((start, end, value))
            occupied.update(range(start, end))
        if len(accepted) == 4:
            break
    if not accepted:
        return None
    for start, end, value in sorted(accepted, reverse=True):
        canonical = canonical[:start] + value + canonical[end:]
    return canonical if canonical != query else None

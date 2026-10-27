"""Conservative missing-accent variants from approved Vietnamese phrases only.

No corpus reads, global ASCII index, guessed diacritics or single-word synonyms.
The original query always remains a separate retrieval branch.
"""
import re
import unicodedata
from functools import cached_property
from time import monotonic

VERSION = 'approved-missing-accents-v1'
WORDS = re.compile(r'[^\W\d_]+', re.UNICODE)


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold().replace('đ', 'd'))
                   if unicodedata.category(c) != 'Mn')


def corpus_accent_variant(query, documents, *, max_tokens=100000, budget_ms=20., clock=monotonic):
    """Restore only an unambiguous multiword phrase observed in eligible bags.

    Caller supplies the already authorized bounded lexical corpus. This is a
    transient candidate operation, never an alias approval or Mongo scan. On
    budget exhaustion it abstains, rather than using incomplete collision data.
    """
    canonical = unicodedata.normalize('NFC', query)
    started = clock()
    words = list(WORDS.finditer(canonical))
    if not 2 <= len(words) <= 160:
        return None
    wanted = {}
    for start in range(len(words)):
        for width in range(2, min(6, len(words) - start) + 1):
            selected = words[start:start + width]
            if any(not canonical[a.end():b.start()].isspace()
                   for a, b in zip(selected, selected[1:], strict=False)):
                continue
            before = canonical[selected[0].start()-1:selected[0].start()] if selected[0].start() else ''
            after = canonical[selected[-1].end():selected[-1].end()+1]
            if before == '_' or after == '_' or before.isdigit() or after.isdigit():
                continue
            raw = ' '.join(m.group().casefold() for m in selected)
            wanted.setdefault(fold(raw), []).append((selected[0].start(), selected[-1].end(), raw))
    observed, used = {}, 0
    first_words = {phrase.split()[0] for phrase in wanted}
    for document in documents.values():
        for tokens in [document.title_tokens, *document.passages]:
            used += len(tokens)
            if used > max_tokens or (clock() - started) * 1000 >= budget_ms:
                return None
            for start in range(len(tokens)):
                if start % 128 == 0 and (clock() - started) * 1000 >= budget_ms:
                    return None
                if fold(tokens[start]) not in first_words:
                    continue
                for width in range(2, min(6, len(tokens) - start) + 1):
                    phrase = ' '.join(tokens[start:start + width])
                    key = fold(phrase)
                    if key in wanted:
                        bucket = observed.setdefault(key, set())
                        if len(bucket) < 2:  # Two values already prove ambiguity.
                            bucket.add(phrase)
    ambiguous = set()
    for key, candidates in observed.items():
        if len(candidates) > 1:
            for start, end, _ in wanted[key]:
                ambiguous.update(range(start, end))
    replacements = []
    for key, candidates in observed.items():
        if len(candidates) != 1:
            continue
        target = next(iter(candidates))
        for start, end, raw in wanted[key]:
            if ambiguous.intersection(range(start, end)):
                continue
            if (raw == target or len(raw) != len(target)
                    or any(fold(a) != a and a != b for a, b in zip(raw, target, strict=True))):
                continue
            replacements.append((start, end, target))
    accepted, occupied = [], set()
    for start, end, target in sorted(replacements, key=lambda row: (-(row[1]-row[0]), row[0])):
        if not occupied.intersection(range(start, end)):
            accepted.append((start, end, target))
            occupied.update(range(start, end))
        if len(accepted) == 4:
            break
    for start, end, target in sorted(accepted, reverse=True):
        canonical = canonical[:start] + target + canonical[end:]
    return canonical if accepted else None


class AccentAliases:
    def __init__(self, dictionary):
        self.dictionary = dictionary

    @cached_property
    def phrases(self):
        result = {}
        for terms in self.dictionary.aliases.values():
            for term in terms:
                if (term.language == 'vi' and term.kind != 'abbreviation'
                        and fold(term.normalized) != term.normalized
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

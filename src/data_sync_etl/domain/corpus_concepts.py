"""Bounded candidate extraction. Occurrence/parentheses are NEVER an approval."""
import re
import unicodedata
from dataclasses import asdict, dataclass
from itertools import combinations
from time import time

from data_sync_etl.domain.concepts import default_dictionary, normalize
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import QUERY_STOPWORDS, surface_tokens
from data_sync_etl.ports.concept_corpus import ConceptBlocked, ResourceEvidence

EXTRACTOR = 'corpus-phrases-v5'
POLICY = 'public-explicit-reviewed-v1'
FORBIDDEN = re.compile(r'https?://|www\.|\S+@\S+|[<>]|\b(?:select|insert|drop)\b.*\b(?:from|table|into)\b|'
    r'approve\s+me|ignore\s+(?:all|previous)|bỏ\s+kiểm\s+duyệt|(?:password|secret|token)\s*[=:]', re.I)
PAIR = re.compile(r'(?:^|[\n;:.])\s*([^()\n;:.]{2,100}?)\s*\(([^()\n]{2,80})\)', re.M)
DEFINITION = re.compile(r'(?:^|[\n;:.])\s*([^()\n;:.]{2,100}?)\s+(?:nghĩa là|means|signifie)\s+([^()\n;:.]{2,100})', re.I)
SYNONYM = re.compile(r'(?:^|[\n;:.])\s*([^()\n;:.]{2,100}?)\s+(?:còn gọi là|also called|aussi appelé)\s+([^()\n;:.]{2,100})', re.I)
LABELLED = re.compile(r'(?:^|[|;])\s*(vi|en|fr)\s*:\s*([^|;\n]{2,100})', re.I)
NEGATED_PAIR = re.compile(r'\bnot\b|\bkhông\s+(?:phải|là|có nghĩa)|\bne\b.+\bpas\b', re.I)
VI_WORDS = frozenset('của và là bộ nhớ dữ liệu đệm trí tuệ nhân tạo phân số quang hợp học máy'.split())
FR_WORDS = frozenset('le la les des une un mémoire apprentissage automatique banque photosynthèse'.split())
EN_WORDS = frozenset('the of and memory cache learning machine artificial intelligence cell bank spreadsheet'.split())


def language(text):
    known = {term.language for term in default_dictionary().aliases.get(normalize(text), [])}
    if len(known) == 1:
        return next(iter(known))
    tokens = set(surface_tokens(text))
    scores = {key: len(tokens & words) for key, words in [('vi', VI_WORDS), ('en', EN_WORDS), ('fr', FR_WORDS)]}
    if any(c in text.casefold() for c in 'ăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ'):
        return 'vi'
    ranked = sorted(scores, key=lambda key: -scores[key])
    return ranked[0] if scores[ranked[0]] > scores[ranked[1]] else 'und'


def safe_phrase(text, max_chars=120):
    text = unicodedata.normalize('NFC', text).strip()
    if not 2 <= len(text) <= max_chars or FORBIDDEN.search(text):
        return None
    words = surface_tokens(text)
    if not 1 <= len(words) <= 6 or not any(c.isalpha() for c in text):
        return None
    if sum(c.isdigit() for c in text) > max(2, len(text) // 4) or len(set(words)) < max(1, len(words) // 2):
        return None
    if all(word in QUERY_STOPWORDS for word in words):
        return None
    return normalize(text)


def eligible(resource):
    return bool(resource and resource.public is True and resource.active and not resource.deleted
        and resource.published and resource.lifecycle in ('INGESTED', 'APPROVED', 'PUBLISHED')
        and resource.scope in ('offline_fixture', 'verified_public'))


def explicit_pairs(text):
    for line in text.splitlines():
        labelled = [(match[2].strip(), match[1].lower()) for match in LABELLED.finditer(line)]
        if 2 <= len(labelled) <= 3 and len({item[1] for item in labelled}) == len(labelled):
            anchor = min(labelled, key=lambda item: (item[1] != 'en', item[1], item[0]))
            for left, right in combinations(labelled, 2):
                yield left[0], right[0], False, line, [left[1], right[1]], anchor
    for pattern, same_language in ((PAIR, False), (DEFINITION, True), (SYNONYM, True)):
        for match in pattern.finditer(text):
            yield match[1], match[2], same_language, match[0], None, None


def fingerprint(resource):
    return digest(asdict(resource) | {'approved_unit_ids': sorted(resource.approved_unit_ids)})


@dataclass(frozen=True)
class DiscoveryLimits:
    max_chars: int = 100_000
    max_candidates: int = 64
    max_units: int = 100
    max_phrase_words: int = 4
    max_evidence: int = 4


DEFAULT_LIMITS = DiscoveryLimits()


class EligibilityFence:
    def __init__(self, inventory, *, clock=time):
        self.inventory, self.clock = inventory, clock

    def _complete(self):
        if not self.inventory.completeness_verified:
            return False
        attest = getattr(self.inventory, 'attestation', None)
        if attest is not None:
            proof = attest()
            return proof.valid(self.clock()) and proof.revision == self.inventory.token()
        # Legacy frozen synthetic authorities remain test-only, never live proof.
        return self.inventory.scope == 'offline_fixture'

    def capture(self):
        try:
            complete = self._complete()
        except Exception:
            complete = False
        if not complete:
            raise ConceptBlocked('BLOCKED_PRIVACY')
        return self.inventory.token()

    def unchanged(self, token):
        try:
            return self._complete() and token == self.inventory.token()
        except Exception:
            return False

    def verify(self, resource):
        current = self.inventory.current(resource.resource_id)
        return eligible(current) and fingerprint(current) == fingerprint(resource)


class ExtractedEvidenceReader:
    """No OCR fallback when current units exist. No raw pages or media bytes."""
    def __init__(self, limits=DEFAULT_LIMITS):
        self.limits = limits

    def fields(self, resource):
        used = 0
        if resource.metadata_approved:
            for name in ('title', 'description', 'tags'):
                text = resource.fields.get(name, '')
                used += len(text)
                if used > self.limits.max_chars:
                    raise ConceptBlocked('CORPUS_BYTE_BUDGET')
                if text:
                    yield name, '', text
        if resource.ready:
            if len(resource.units) > self.limits.max_units:
                raise ConceptBlocked('CORPUS_UNIT_BUDGET')
            for unit in resource.units:
                if (unit.get('id') not in resource.approved_unit_ids
                        or unit.get('resource_version_id') != resource.version_id or unit.get('is_current') is not True):
                    continue
                text = unit.get('text', '')
                used += len(text)
                if used > self.limits.max_chars:
                    raise ConceptBlocked('CORPUS_BYTE_BUDGET')
                yield 'unit', unit['id'], text


def discover(resource: ResourceEvidence, reader=None, limits=DEFAULT_LIMITS):
    result = {'resource_id': resource.resource_id, 'version_id': resource.version_id,
        'revision': resource.revision, 'fingerprint': fingerprint(resource), 'extractor': EXTRACTOR,
        'policy': POLICY, 'terms': [], 'relations': [], 'eligible': eligible(resource)}
    result['evidence_fingerprint'] = digest([result['fingerprint'], []])
    if not result['eligible']:
        return result
    mentions, relations, seen_fields = {}, {}, set()
    for field, unit_id, raw in (reader or ExtractedEvidenceReader(limits)).fields(resource):
        # Same text from metadata/unit contributes only once in this version.
        text = unicodedata.normalize('NFC', raw)
        text_hash = digest(text)
        if text_hash in seen_fields:
            continue
        seen_fields.add(text_hash)
        evidence = {'version_id': resource.version_id, 'field': field, 'unit_id': unit_id,
            'source_fingerprint': result['fingerprint'], 'text_hash': text_hash, 'scope': resource.scope,
            'extractor': EXTRACTOR, 'source_updated_at': resource.source_updated_at}
        for left_raw, right_raw, same_language, raw_pair, declared_languages, alignment_anchor in explicit_pairs(text):
            left, right = safe_phrase(left_raw), safe_phrase(right_raw)
            if not left or not right or left == right or FORBIDDEN.search(raw_pair) or NEGATED_PAIR.search(raw_pair):
                continue
            langs = declared_languages or [language(left), language(right)]
            kind = ('abbreviation' if right_raw.strip().isupper() else
                    'translation' if 'und' not in langs and langs[0] != langs[1] else
                    'synonym' if same_language and 'und' not in langs else 'related')
            terms = [{'text': label, 'language': lang} for label, lang in zip((left, right), langs, strict=True)]
            identity = [resource.domain, resource.sense, kind, sorted((t['language'], t['text']) for t in terms)]
            cid = 'corpus:' + digest(identity)[:32]
            if cid not in relations and len(relations) < limits.max_candidates:
                seed_ids = [{term.concept_id for term in default_dictionary().aliases.get(item['text'], [])
                             if term.domain == resource.domain} for item in terms]
                known_ids = set.union(*seed_ids)
                # Domain/sense-qualified anchor identity joins separately
                # REVIEWED bilingual pairs; it is not a surface-only concept ID.
                anchor = min(terms, key=lambda item: (item['language'] != 'en', item['language'], item['text']))
                if alignment_anchor and safe_phrase(alignment_anchor[0]):
                    anchor = {'text': safe_phrase(alignment_anchor[0]), 'language': alignment_anchor[1]}
                concept_id = (next(iter(known_ids)) if len(known_ids) == 1 else 'corpus:' +
                    digest(['explicit-anchor', resource.domain, resource.sense, anchor['language'], anchor['text']])[:32])
                relations[cid] = {'id': cid, 'domain': resource.domain, 'sense': resource.sense,
                    'concept_id': concept_id,
                    'kind': kind, 'terms': terms, 'strength': 'E2', 'status': 'pending',
                    'development_rank': (20 if kind == 'translation' else 10 if kind == 'abbreviation' else 0)
                        + (5 if 'und' not in langs else 0) + (3 if field == 'unit' else 2)
                        + (2 if resource.sense else 0),
                    'rank_is_probability': False,
                    'evidence': [evidence], 'binding': digest([POLICY, EXTRACTOR, identity, concept_id, result['fingerprint'], text_hash])}
        # Filtered technical n-grams are mention candidates only, never concepts.
        words = surface_tokens(text)
        for start in range(len(words)):
            if len(mentions) >= limits.max_candidates:
                break
            for size in range(2, limits.max_phrase_words + 1):
                phrase = safe_phrase(' '.join(words[start:start + size]))
                if phrase:
                    key = digest([language(phrase), phrase, resource.domain, resource.sense])
                    mentions.setdefault(key, {'key': key, 'term': phrase, 'language': language(phrase),
                        'domain': resource.domain, 'sense': resource.sense, 'status': 'pending', 'evidence': [evidence]})
                if len(mentions) >= limits.max_candidates:
                    break
    result['terms'] = list(mentions.values())
    result['relations'] = sorted(relations.values(), key=lambda item: (-item['development_rank'], item['id']))
    result['evidence_fingerprint'] = digest([result['fingerprint'], sorted(seen_fields)])
    return result


def valid_relation(candidate, authority_fingerprint):
    try:
        terms, evidence = candidate['terms'], candidate['evidence'][0]
        if len(terms) != 2 or any(safe_phrase(term['text']) != term['text'] for term in terms):
            return False
        if evidence['source_fingerprint'] != authority_fingerprint or evidence['scope'] not in ('offline_fixture', 'verified_public'):
            return False
        identity = [candidate['domain'], candidate['sense'], candidate['kind'],
                    sorted((term['language'], term['text']) for term in terms)]
        return (candidate['id'] == 'corpus:' + digest(identity)[:32]
                and candidate['binding'] == digest([POLICY, EXTRACTOR, identity, candidate['concept_id'], authority_fingerprint, evidence['text_hash']]))
    except (KeyError, IndexError, TypeError):
        return False

"""Admission for weak dense hits with independent, verified phrase evidence.

Never creates a vector score or searches another corpus. Call only with fresh,
authorized source text; a title/denied sibling is not source passage evidence.
"""
from data_sync_etl.domain.lexical import QUERY_STOPWORDS, TOKEN, surface_tokens
from data_sync_etl.domain.query_variants import fold

VERSION = 'verified-source-anchor-dense-v1'


def query_anchors(query):
    words = list(TOKEN.finditer(query))
    if (2 <= len(words) <= 4 and all(w.group().casefold() not in QUERY_STOPWORDS for w in words)
            and all(query[a.end():b.start()].isspace() for a, b in zip(words, words[1:], strict=False))):
        return [surface_tokens(query)]
    anchors = []
    run, previous = [], None
    for match in words:
        word = match.group()
        cf = word.casefold()
        if previous is not None and not query[previous:match.start()].isspace():
            if 2 <= len(run) <= 4:
                anchors.append([w.casefold() for w in run])
            run = []
        if cf not in QUERY_STOPWORDS and len(cf) > 1:
            run.append(word)
            if len(run) == 4:
                anchors.append([w.casefold() for w in run])
                run = run[2:]
        else:
            if 2 <= len(run) <= 4:
                anchors.append([w.casefold() for w in run])
            run = []
        previous = match.end()
    if 2 <= len(run) <= 4:
        anchors.append([w.casefold() for w in run])

    upper_anchors, run, previous = [], [], None
    for match in words:
        word = match.group()
        if previous is not None and not query[previous:match.start()].isspace():
            if 2 <= len(run) <= 4:
                upper_anchors.append([w.casefold() for w in run])
            run = []
        if word[0].isupper() and word.casefold() not in QUERY_STOPWORDS:
            run.append(word)
        else:
            if 2 <= len(run) <= 4:
                upper_anchors.append([w.casefold() for w in run])
            run = []
        previous = match.end()
    if 2 <= len(run) <= 4:
        upper_anchors.append([w.casefold() for w in run])

    for a in upper_anchors:
        if a not in anchors:
            anchors.append(a)

    deduped = []
    seen = set()
    for a in anchors:
        key = tuple(a)
        if key not in seen:
            seen.add(key)
            deduped.append(a)
    return deduped[:8]


def anchored_dense_match(query, text, score, *, floor, dictionary=None, policy=None, expansion=None):
    if score < floor or not text:
        return False
    matches = list(TOKEN.finditer(text))
    tokens = [fold(match.group()) for match in matches]
    for anchor in query_anchors(query):
        words = [fold(word) for word in anchor]
        if sum(map(len, words)) < 6:
            continue
        for start in range(len(tokens) - len(words) + 1):
            if tokens[start:start + len(words)] != words:
                continue
            selected = matches[start:start + len(words)]
            if all(text[a.end():b.start()].isspace() and '\n\n' not in text[a.end():b.start()]
                   for a, b in zip(selected, selected[1:], strict=False)):
                return True
    if dictionary is not None and policy is not None and policy.enabled and expansion is not None:
        # Equivalence is from the existing approved dictionary, not translation
        # inference or a co-occurring word. Dense score still supplies ranking.
        return any(row.get('concept_id') and row.get('query_concept_id')
                   and row['relation'] in ('original', 'synonym', 'translation', 'abbreviation', 'equivalent', 'spelling')
                   for row in dictionary.evidence(expansion, text, policy=policy))
    return False

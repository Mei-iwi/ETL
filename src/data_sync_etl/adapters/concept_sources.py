"""Offline adapters for saved Wikidata EntityData and ConceptNet API JSON.

External data is always pending, never merged into the production dictionary.
Fetch/pagination belong to the operator; search has no network dependency.
"""
from data_sync_etl.domain.concepts import Concept, Relation, Snapshot, Source, Term, normalize
from data_sync_etl.domain.indexing import digest


def _concept(cid, labels, aliases, definition, domain, sources):
    terms, seen = [], set()
    for language, text, kind in [*( (lang, value, 'preferred') for lang, value in labels.items()),
                                 *((lang, value, 'synonym') for lang, values in aliases.items() for value in values)]:
        key = (language, normalize(text))
        if key in seen or not key[1]:
            continue
        seen.add(key)
        terms.append(Term(text=text, normalized=key[1], language=language, kind=kind,
                          concept_id=cid, domain=domain, sources=sources, confidence=0.9,
                          review_status='pending'))
    return Concept(concept_id=cid, preferred_labels=labels, definition=definition,
                   domain=domain, terms=terms, sources=sources, review_status='pending', version='1')


def wikidata(payload, *, domain, version):
    concepts = {}
    for qid, entity in payload['entities'].items():
        labels = {lang: value['value'] for lang, value in entity.get('labels', {}).items() if lang in ('vi', 'en')}
        if not labels:
            continue
        aliases = {lang: [a['value'] for a in values] for lang, values in entity.get('aliases', {}).items()
                   if lang in ('vi', 'en')}
        descriptions = entity.get('descriptions', {})
        description = descriptions.get('vi', descriptions.get('en', {})).get('value', 'Definition requires review')
        cid = f'wd:{qid}'
        concepts[cid] = _concept(cid, labels, aliases, description, domain,
                                 [Source(name='wikidata', identifier=qid)])
    skipped = []
    for cid, concept in concepts.items():
        entity = payload['entities'][cid.removeprefix('wd:')]
        # P279 is subclass -> broader; P31 instance-of is kept conservatively
        # as related, with its property ID in provenance, never as synonym.
        for prop, kind in [('P279', 'broader'), ('P31', 'related')]:
            for claim in entity.get('claims', {}).get(prop, []):
                if claim.get('rank') == 'deprecated':
                    continue
                value = claim.get('mainsnak', {}).get('datavalue', {}).get('value', {})
                target = 'wd:' + value.get('id', '') if isinstance(value, dict) else ''
                if target not in concepts:
                    skipped.append({'source': cid, 'property': prop, 'target': target})
                    continue
                concept.relations.append(Relation(source_id=cid, target_id=target, kind=kind,
                    confidence=0.9, sources=[Source(name='wikidata', identifier=f'{cid}/{prop}/{target}')],
                    review_status='pending', allow_expansion=False))
    return Snapshot(version=version, concepts=list(concepts.values())), skipped


def conceptnet(payload, *, domain, version):
    concepts, skipped = {}, []
    mapping = {'/r/Synonym': 'equivalent', '/r/IsA': 'broader', '/r/RelatedTo': 'related'}
    for edge in payload['edges']:
        relation = edge['rel']['@id']
        if relation not in mapping or any(node.get('language') not in ('vi', 'en') for node in (edge['start'], edge['end'])):
            skipped.append(edge.get('@id', relation))
            continue
        ids = []
        for node in (edge['start'], edge['end']):
            uri, language = node['@id'], node['language']
            cid = 'cn:' + digest(uri)[:24]
            ids.append(cid)
            if cid not in concepts:
                concepts[cid] = _concept(cid, {language: node['label']}, {},
                    f'ConceptNet node {uri}; sense definition requires review', domain,
                    [Source(name='conceptnet', identifier=uri)])
        kind = mapping[relation]
        if relation == '/r/Synonym' and edge['start']['language'] != edge['end']['language']:
            kind = 'translation'
        source = [Source(name='conceptnet', identifier=edge['@id'])]
        if edge.get('license'):
            source.append(Source(name='license', identifier=edge['license']))
        concepts[ids[0]].relations.append(Relation(source_id=ids[0], target_id=ids[1], kind=kind,
            direction='bidirectional' if relation in ('/r/Synonym', '/r/RelatedTo') else 'outgoing',
            confidence=0.9, sources=source, review_status='pending', allow_expansion=False))
    return Snapshot(version=version, concepts=list(concepts.values())), skipped

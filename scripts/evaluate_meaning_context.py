"""Private, READ ONLY local comparison using existing reviewed/draft query labels.

No SOURCE reads, new embeddings of documents, schema changes or index writes.
The baseline is current stored vectors plus the old dictionary and raw max
cosine selection. Labels remain exploratory, not independent human acceptance.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine

from data_sync_etl.application.content_bags import bag_structure
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.concepts import Dictionary, Snapshot
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import analysis_scope, bm25, query_terms
from data_sync_etl.domain.meaning_bags import match_meaning
from data_sync_etl.domain.search import cosine
from data_sync_etl.domain.search_access import SearchAccess


def grouped(rows):
    best = {}
    for resource, score, source in rows:
        if resource not in best or score > best[resource][0]:
            best[resource] = score, source
    return [(resource, *values) for resource, values in sorted(best.items(),
        key=lambda row: (-row[1][0], row[0]))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-ref', required=True, help='Commit containing the previous concept dictionary')
    args = parser.parse_args()
    if re.fullmatch(r'[0-9a-fA-F]{7,40}', args.baseline_ref) is None:
        parser.error('Baseline must be a commit hash, not a branch or option')
    if not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()) or args.output.exists():
        parser.error('New private .test-tmp output required')
    settings = Settings()
    if any(urlsplit(url).hostname not in ('127.0.0.1', 'localhost', '::1')
           for url in (settings.database_url, settings.mongo_uri)):
        parser.error('Local audit only')
    old_snapshot = subprocess.run(['git', 'show', args.baseline_ref + ':src/data_sync_etl/domain/data/concepts.json'],
        capture_output=True, check=True).stdout.decode('utf-8')
    before_dictionary = Dictionary(Snapshot.model_validate_json(old_snapshot))
    container = Container(settings=settings, engine=create_engine(settings.database_url,
        hide_parameters=True, connect_args={'options': '-c default_transaction_read_only=on', 'connect_timeout': 5}))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        import numpy as np
        import torch
        torch.set_num_threads(2)
        access = SearchAccess('authenticated_admin')
        with container.uow() as repo:
            metadata = container.search._metadata(repo, {}, access, settings.search_max_bags + 1)
            allowed = set()
            for row in metadata:
                snapshot = container.search._ready_snapshot(repo, row['version_id'])
                if snapshot:
                    units = container.search._allowed_units(repo, snapshot, access)
                    allowed.update(root for root, leaves in bag_structure(snapshot[2]).items() if set(leaves) <= units)
        resources = {row['version_id']: row['resource_id'] for row in metadata}
        bags = [bag for bag in container.content_bags.search_candidates(list(resources),
            model=f'{container.indexing.embedding.model_id}@{container.indexing.embedding.version}',
            limit=settings.search_max_bags + 1) if bag.id in allowed]
        if not bags or len(bags) > settings.search_max_bags or any(b.meanings is None for b in bags):
            raise ValueError('Complete bounded Meaning Bag corpus required')
        offsets, vectors = [], []
        for bag in bags:
            offsets.append(len(vectors))
            vectors.extend(member.embedding for member in bag.meanings.members)
        matrix = np.asarray(vectors, dtype=np.float64)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        specifications = []
        for path in ('research/semantic-quality/query-specification.json',
                     'research/semantic-quality/optimization/queries-v1.json'):
            specifications.extend(json.loads(Path(path).read_text(encoding='utf-8'))['queries'])
        container.indexing.embedding.prepare()
        output = {'label_status': 'EXPLORATORY_EXISTING_DRAFT_LABELS_NOT_INDEPENDENT_ACCEPTANCE',
                  'scope': 'READONLY_LOCAL_EXISTING_BAGS_NO_LIVE_HTTP',
                  'baseline_ref': args.baseline_ref,
                  'corpus_fingerprint': digest([(b.id, b.content_fingerprint, b.meanings.fingerprint) for b in bags]),
                  'resources': len(resources), 'bags': len(bags), 'rows': []}
        dictionary = container.search.dictionary
        policy = settings.search_expansion
        for spec in specifications:
            query = spec['query']
            vector = container.indexing.embedding.embed(query)
            normalized = np.asarray(vector) / np.linalg.norm(vector)
            scores = matrix @ normalized
            context = {'query': query, 'keywords': query_terms(query), 'dictionary': dictionary,
                       'policy': policy, 'expansion': dictionary.expand(query, policy=policy),
                       'margin': settings.search_meaning_context_margin}
            old_hits, new_hits = [], []
            for i, bag in enumerate(bags):
                begin = offsets[i]
                end = begin + len(bag.meanings.members)
                old_index = int(np.argmax(scores[begin:end]))
                old_score = float(scores[begin + old_index])
                new_score, member, _ = match_meaning(vector, bag.meanings, context=context)
                def admitted(value, vector=vector, bag=bag):
                    return value >= settings.search_vector_min_similarity or (
                        settings.search_meaning_context_fallback_enabled
                        and value >= settings.search_meaning_fallback_min_similarity
                        and cosine(vector, bag.embedding) >= settings.search_meaning_fallback_min_context_similarity)
                resource = resources[bag.resource_version_id]
                if admitted(old_score):
                    old_hits.append((resource, old_score, bag.meanings.members[old_index].text))
                if admitted(new_score):
                    new_hits.append((resource, new_score, member.text))
            lexical = {bag.id: bag.lexical for bag in bags}
            with analysis_scope():
                old_bow = bm25(query, lexical, dictionary=before_dictionary, policy=policy)
                new_bow = bm25(query, lexical, dictionary=dictionary, policy=policy,
                               min_coverage=settings.search_bow_min_coverage)
            before_bow = grouped([(resources[b.resource_version_id], old_bow[b.id], '') for b in bags if b.id in old_bow])
            after_bow = grouped([(resources[b.resource_version_id], new_bow[b.id], '') for b in bags if b.id in new_bow])
            positive = {rid for annotation in spec['annotations'] if annotation['grade'] >= 2
                        for rid in annotation['resource_ids']}
            row = {'query_id': spec['query_id'], 'category': spec['category'], 'language': spec['language'],
                   'relevant_resources': sorted(positive), 'before_vector': grouped(old_hits)[:5],
                   'after_vector': grouped(new_hits)[:5], 'before_bow': before_bow[:5], 'after_bow': after_bow[:5]}
            output['rows'].append(row)
            args.output.write_text(json.dumps(output, ensure_ascii=True, indent=2), encoding='utf-8')
            print(json.dumps({'query_id': spec['query_id'], 'completed': len(output['rows'])}), flush=True)
    finally:
        container.close()


if __name__ == '__main__':
    main()

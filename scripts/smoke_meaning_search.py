"""Read-only local-corpus search smoke; reports IDs/scores, not source text."""

import argparse
import json
from pathlib import Path

from data_sync_etl.application.search import QueryEmbeddingContext
from data_sync_etl.composition import Container
from data_sync_etl.domain.search_access import SearchAccess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', action='append')
    parser.add_argument('--output', type=Path, default=Path('storage/benchmarks/meaning-search-local.json'))
    args = parser.parse_args()
    import torch

    torch.set_num_threads(2)
    c = Container()
    try:
        if not c.settings.index_meaning_bags_enabled:
            raise ValueError('Automatic meaning mode must be enabled')
        c.indexing.bags.ensure_meanings_ready()
        c.indexing.embedding.prepare()
        versions = sorted(c.indexing.bags.database.content_bags.distinct('resource_version_id', {'is_current': True}))
        readiness = [{'version_id': version, 'status': c.indexing.status(version)['index_status']} for version in versions]
        if any(row['status'] != 'READY' for row in readiness):
            raise ValueError('Selected local corpus has versions not ready for semantic smoke')
        access = SearchAccess('local_demo')
        rows = []
        for query in args.query or ['machine learning', 'apprentissage automatique', '機械学習',
                      'hệ thống học quy luật từ dữ liệu để dự đoán', 'How to bake a chocolate cake in an oven']:
            with QueryEmbeddingContext() as query_context:
                for method in ['vector', 'hybrid']:
                    result = c.search.search(query, method, access=access, _query_context=query_context,
                                             _batch_texts=True, _batch_bags=True)
                    row = {'query': query, 'method': method, 'total': result['total'],
                           'ready_bags': result['ready_bag_count'], 'algorithm': result['ranking_algorithm']}
                    if result['items']:
                        item = result['items'][0]
                        context = c.search.context(item['context_id'], neighbors=0, access=access)
                        selected = next(chunk for chunk in context['chunks'] if chunk['selected'])
                        row['top'] = {'resource_id': item['resource_id'], 'bag_id': item['bag_id'],
                                      'evidence': item['context_evidence'], 'scores': item['score_components'],
                                      'snippet_in_selected_context': item['snippet'] in selected['text']}
                        if not row['top']['snippet_in_selected_context']:
                            raise ValueError('Search/context evidence mismatch')
                    rows.append(row)
                    print(json.dumps(row, ensure_ascii=True), flush=True)
        report = {'readiness': readiness, 'queries': rows,
                  'limitations': ['Live corpus smoke with no independent relevance labels; not a quality score.']}
        output = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        c.close()


if __name__ == '__main__':
    main()

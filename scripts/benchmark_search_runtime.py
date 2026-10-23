"""Bounded READ ONLY local Search measurements; no SOURCE reads or index writes.

Results are private and must be written below .test-tmp. This measures service
work with an administrative audit scope, not browser authentication or HTTP.
"""
import argparse
import json
import statistics
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

from sqlalchemy import create_engine

from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.lexical import query_terms
from data_sync_etl.domain.search_access import SearchAccess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=1, choices=range(1, 6))
    parser.add_argument('--threads', type=int, choices=range(1, 17))
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()) or args.output.exists():
        parser.error('Choose a new private output below .test-tmp')
    settings = Settings()
    if (urlsplit(settings.database_url).hostname not in ('127.0.0.1', 'localhost', '::1')
            or urlsplit(settings.mongo_uri).hostname not in ('127.0.0.1', 'localhost', '::1')):
        parser.error('Only local MASTER/Mongo audit is supported')
    engine = create_engine(settings.database_url, hide_parameters=True, connect_args={
        'connect_timeout': 5, 'options': '-c default_transaction_read_only=on'})
    container = Container(settings=settings, engine=engine)
    rows = []
    report = {'level': 'warmed-service-admin-readonly', 'samples': args.samples,
              'threads_override': args.threads, 'results': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        import torch
        if args.threads:
            torch.set_num_threads(args.threads)
        query_terms('warmup')
        container.indexing.embedding.prepare()
        for query in ('green leaf', 'How to add fractions with different denominators?',
                      'quantum cryptocurrency banana spaceship'):
            for method in ('bow', 'vector'):
                times, response, error = [], None, None
                for _ in range(args.samples):
                    started = perf_counter()
                    try:
                        response = container.search.search(query, method,
                            access=SearchAccess('authenticated_admin'),
                            _batch_texts=True, _batch_bags=True)
                    except Exception as exc:
                        error = getattr(exc, 'code', type(exc).__name__)
                    times.append(perf_counter() - started)
                row = {'query': query, 'method': method, 'seconds': times,
                       'median_seconds': statistics.median(times), 'error': error,
                       'total': response['total'] if response else None,
                       'items': response['items'] if response else [],
                       'readiness': response['readiness'] if response else None}
                rows.append(row)
                args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
                print(json.dumps({k: v for k, v in row.items() if k not in ('items', 'readiness')}), flush=True)
    finally:
        container.close()


if __name__ == '__main__':
    main()

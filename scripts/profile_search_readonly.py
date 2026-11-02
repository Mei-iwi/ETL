"""Read-only search stage timings; never prints result text, tokens or credentials."""

import argparse
from collections import defaultdict
from time import monotonic

from data_sync_etl.composition import Container
from data_sync_etl.domain.search_access import SearchAccess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--query', default='vector')
    parser.add_argument('--methods', nargs='+', default=['bow', 'hybrid'])
    parser.add_argument('--repeat', type=int, choices=range(1, 6), default=2)
    args = parser.parse_args()
    container = Container()
    print({'search_timeout_seconds': container.settings.search_timeout_seconds}, flush=True)
    timings = defaultdict(lambda: [0, 0.])

    def instrument(owner, name):
        original = getattr(owner, name, None)
        if not callable(original):
            return

        def measured(*args, **kwargs):
            started = monotonic()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = monotonic() - started
                timings[name][0] += 1
                timings[name][1] += elapsed
                if elapsed > 1:
                    print({'stage': name, 'seconds': round(elapsed, 3)}, flush=True)
        setattr(owner, name, measured)

    try:
        for name in ('_content_search', '_ready_snapshot', '_authorized_metadata', '_query_vector'):
            instrument(container.search, name)
        if container.search.metadata:
            for name in ('retrieve', 'snapshots', 'final_validate'):
                instrument(container.search.metadata, name)
        for name in ('search_phase', 'search_final_phase', 'search_phase_from_candidates', 'search_candidates'):
            instrument(container.search.bags, name)
        # Model initialization is reported separately from steady-state latency.
        started = monotonic()
        from data_sync_etl.domain.lexical import analyze
        analyze('Kiểm tra bộ phân tích')
        print({'lexical_prepare_seconds': round(monotonic() - started, 3)}, flush=True)
        if any(method != 'bow' for method in args.methods):
            started = monotonic()
            container.indexing.embedding.prepare()
            print({'model_prepare_seconds': round(monotonic() - started, 3)}, flush=True)
        for iteration, method in ((iteration, method) for iteration in range(1, args.repeat + 1) for method in args.methods):
            timings.clear()
            started = monotonic()
            try:
                result = container.search.search_http(args.query, method, access=SearchAccess('authenticated_admin'))
                print({'method': method, 'iteration': iteration, 'total': result['total'], 'status': result.get('message'),
                       'seconds': round(monotonic() - started, 3)}, flush=True)
            except Exception as exc:
                print({'method': method, 'error': type(exc).__name__, 'code': getattr(exc, 'code', None),
                       'seconds': round(monotonic() - started, 3)}, flush=True)
            print({'timings': dict(timings)}, flush=True)
    finally:
        container.close()


if __name__ == '__main__':
    main()

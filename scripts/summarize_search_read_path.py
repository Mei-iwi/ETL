"""Summarize sanitized physical traces; never connect to a database or read dotenv."""

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / 'docs/audits/search-read-path'
    metrics = json.loads((folder / 'baseline_metrics.json').read_text(encoding='utf-8'))
    if metrics['status'] != 'MEASURED' and len(metrics.get('requests', [])) < 1:
        raise ValueError('Physical traces required; incomplete runs must be reported explicitly')
    target = folder / 'hotspots.csv'
    if target.exists():
        raise FileExistsError('Refusing to overwrite prior hotspot summary')
    groups = defaultdict(list)
    for row in metrics['requests']:
        if row['case'] == 'aa' and row['profiler']:
            groups[(row['corpus'], row['method'])].append(row)
    fields = ['corpus', 'method', 'backend', 'operation', 'collection_or_tables', 'callsite',
              'calls_per_request', 'duplicate_exact_calls_per_request', 'driver_ms_per_request',
              'percent_profiled_wall', 'samples']
    output = []
    for (corpus, method), requests in sorted(groups.items()):
        buckets = defaultdict(list)
        for row in requests:
            for op in row['operations']:
                stack = op['callsites']
                name = next((v for v in stack if any(s in v for s in (
                    'application/indexing.py:65:', 'application/indexing.py:124:',
                    'adapters/mongo/foundation_store.py:339:', 'application/indexing.py:129:',
                    'application/search.py:214:'))), stack[0] if stack else 'unknown')
                tables = op.get('collection', '|'.join(op.get('tables', [])))
                buckets[(op['backend'], op['operation'], tables, name)].append(op)
        for (backend, operation, tables, callsite), operations in sorted(buckets.items()):
            # Exact means identical command+parameters within that request only.
            duplicates = 0
            for row in requests:
                selected = [v for v in row['operations'] if v in operations]
                duplicates += sum(n - 1 for n in Counter(v['exact'] for v in selected).values())
            ms = sum(v['ms'] for v in operations) / len(requests)
            output.append(dict(zip(fields, [corpus, method, backend, operation, tables, callsite,
                len(operations) / len(requests), duplicates / len(requests), ms,
                ms / statistics.mean(v['total_ms'] for v in requests) * 100, len(requests)], strict=True)))
    with target.open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output)
    print(json.dumps({'physical_status': metrics['status'], 'requests': len(metrics['requests']),
        'parity_pairs': metrics['parity_pairs'], 'hotspot_rows': len(output)}, indent=2))


if __name__ == '__main__':
    main()

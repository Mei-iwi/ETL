"""Actual component timings on a tiny DRAFT fixture; no real dense/HTTP claims."""

import json
import math
import platform
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event

from data_sync_etl.adapters.persistence.search_reader import SearchRepositoryMixin
from data_sync_etl.domain.search_access import SearchAccess
from tests.indexing_support import setup_index
from tests.unit import test_metadata_search

metadata = test_metadata_search.metadata


def test_metadata_component_benchmark(metadata):
    c, _, fraction, service = metadata
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-2', {
            'title': 'Bài giảng về quang hợp ở thực vật', 'mongo_id': 'desc-2', 'source_origin': 'LOCAL'})
        repo.insert('resource_versions', {'resource_id': 'resource-2', 'version_number': 1,
            'lifecycle_status': 'INGESTED', 'storage_bucket': 'fixture', 'storage_object_key': 'registered.mp3', 'source_hash': 'b' * 64})
    plant = SimpleNamespace(id='desc-2', description='Âm thanh giảng về ánh sáng trong quang hợp ở cây xanh.',
                            tags=['quang hợp', 'thực vật', 'ánh sáng'])
    service.documents.get_resource = lambda key: {'desc': fraction, 'desc-2': plant}[key]
    index = setup_index(c)
    index.bags.search_candidates = lambda *args, **kwargs: []
    cases = [
        ('vi_title', 'cộng hai phân số khác mẫu số', 'resource-1'),
        ('vi_description', 'quy đồng mẫu số', 'resource-1'),
        ('vi_tags', 'phép cộng', 'resource-1'),
        ('vi_plant', 'quang hợp ở thực vật', 'resource-2'),
        ('vi_accentless', 'cach cong hai phan so khac mau', 'resource-1'),
        ('en', 'How to add fractions with different denominators?', 'resource-1'),
        ('fr', 'Comment additionner des fractions ?', 'resource-1'),
        ('mixed', 'video cach add fractions khac mau', 'resource-1'),
        ('hard_negative_subtract', 'phép trừ phân số', None),
        ('hard_negative_respiration', 'hô hấp ở cây', None),
        ('no_answer', 'quantum cryptography', None),
    ]
    access = SearchAccess('local_demo')
    engine = c.engine
    counter = [0]
    def count(*args):
        counter[0] += 1
    event.listen(engine, 'before_cursor_execute', count)
    try:
        def run(enabled, include_content):
            c.settings.search_metadata_enabled = enabled
            c.settings.search_metadata_content_enabled = include_content
            rows, times = [], []
            for group, query, expected in cases:
                durations, statements = [], []
                for _ in range(3):
                    start, before = perf_counter(), counter[0]
                    result = c.search.search(query, 'bow', access=access)
                    durations.append(perf_counter() - start)
                    statements.append(counter[0] - before)
                times += durations
                rows.append(dict(group=group, expected=expected, ids=[row['resource_id'] for row in result['items']],
                    fields=[row.get('matched_fields', []) for row in result['items']], seconds=durations, sql_statements=statements))
            positives = [row for row in rows if row['expected']]
            ranks = [row['ids'].index(row['expected']) + 1 if row['expected'] in row['ids'] else math.inf for row in positives]
            negatives = [row for row in rows if not row['expected']]
            return {'queries': rows, 'metrics': {
                'P@1': sum(rank == 1 for rank in ranks) / len(ranks),
                'Recall@5': sum(rank <= 5 for rank in ranks) / len(ranks),
                'MRR@10': sum(1 / rank if rank <= 10 else 0 for rank in ranks) / len(ranks),
                'nDCG@10': sum(1 / math.log2(rank + 1) if rank <= 10 else 0 for rank in ranks) / len(ranks),
                'NoAnswerFPR': sum(bool(row['ids']) for row in negatives) / len(negatives),
                'DuplicateCardRate': sum(len(row['ids']) != len(set(row['ids'])) for row in rows) / len(rows)},
                'latency': {'p50_seconds': median(times), 'p95_seconds': sorted(times)[math.ceil(.95 * len(times)) - 1],
                            'cold_first_seconds': times[0], 'samples': len(times)}}
        before = run(False, True)
        full = service.reconcile(apply=True)
        incremental = service.reconcile(apply=True)
        def legacy_snapshots(repo, filters, access, *, limit):
            rows = repo.search_versions(filters, limit=limit, metadata_access=access)
            return [(row, repo.get('master_learning_resources', row['resource_id']),
                repo.get('resource_versions', row['version_id']), repo.semantic_metadata(row['resource_id']),
                repo.find('source_file_imports', {'target_version_id': row['version_id']}, limit=2)) for row in rows]
        with patch.object(SearchRepositoryMixin, 'metadata_snapshots', legacy_snapshots):
            unbatched = run(True, False)
        after = run(True, False)
        combined = run(True, True)
        assert [(row['ids'], row['fields']) for row in unbatched['queries']] == [
            (row['ids'], row['fields']) for row in after['queries']]
        result = {'environment': platform.platform(), 'qrels_status': 'DRAFT developer assertions',
            'corpus': 'two synthetic registered metadata-only versions, no content', 'query_count': len(cases),
            'methods': {'content_only_bow': before, 'metadata_only_bow': after, 'combined_bow': combined},
            'performance_only_ablation': {'unbatched_metadata': unbatched, 'batched_metadata': after,
                                         'result_id_and_field_parity': True},
            'index_full': full, 'index_incremental': incremental, 'real_model': 'BLOCKED: FlagEmbedding unavailable',
            'scope': 'in-process SQLite, memory index, real pinned analyzer; not HTTP or production DB',
            'unmeasured': ['RSS/CPU', 'real Mongo writes', 'staging HTTP', 'dense quality', 'concurrency']}
        destination = Path('research/metadata-retrieval')
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'benchmark-current.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        assert full['writes'] == 2 and incremental['unchanged'] == 2 and incremental['embeds'] == 0
    finally:
        event.remove(engine, 'before_cursor_execute', count)

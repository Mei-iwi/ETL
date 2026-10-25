"""Explicit offline real-checkpoint probe; never downloads or runs by default."""

import json
import math
import os
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
from data_sync_etl.domain.search import cosine, rrf
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit import test_metadata_search

metadata = test_metadata_search.metadata


@pytest.mark.skipif(os.environ.get('ETL_METADATA_REAL_MODEL') != '1', reason='Explicit offline checkpoint probe only')
def test_offline_metadata_bge_m3_actual_checkpoint(metadata, monkeypatch):
    c, _, fraction, service = metadata
    root = Path(__file__).resolve().parents[2]
    checkpoint = root / 'Models/bge-m3'
    revision = (checkpoint / 'etl-model-revision.txt').read_text().strip()
    model = BgeM3Embedding(checkpoint, revision, batch_size=2, max_tokens=512, timeout_seconds=300)
    assert model.is_available(), 'Pinned offline BGE-M3 runtime not available'
    started = perf_counter()
    model.prepare()
    resident = model._model
    prepare_seconds = perf_counter() - started
    counts = {'document_texts': 0, 'query_texts': 0}
    documents, query_encoder = model.embed_documents, model.embed_queries
    def encode_documents(texts):
        counts['document_texts'] += len(texts)
        return documents(texts)
    def encode_queries(texts):
        counts['query_texts'] += len(texts)
        return query_encoder(texts)
    monkeypatch.setattr(model, 'embed_documents', encode_documents)
    monkeypatch.setattr(model, 'embed_queries', encode_queries)
    service.embedding = model
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-2', {
            'source_origin': 'LOCAL', 'title': 'Bài giảng về quang hợp ở thực vật', 'mongo_id': 'desc-2'})
        repo.insert('resource_versions', {'resource_id': 'resource-2', 'version_number': 1,
            'lifecycle_status': 'INGESTED', 'storage_bucket': 'test', 'storage_object_key': 'registered.mp3', 'source_hash': 'b' * 64})
    plant = SimpleNamespace(id='desc-2', description='Âm thanh giảng về vai trò ánh sáng trong quang hợp ở cây xanh.',
                            tags=['quang hợp', 'thực vật', 'ánh sáng'])
    service.documents.get_resource = lambda key: {'desc': fraction, 'desc-2': plant}[key]
    full = service.reconcile(apply=True, dense=True)
    assert full['embeds'] == 2 and full['dense_failed'] == 0
    assert service.reconcile(apply=True, dense=True)['unchanged'] == 2
    cases = [('vi', 'cộng hai phân số khác mẫu số', 'resource-1'),
             ('accentless', 'cach cong hai phan so khac mau', 'resource-1'),
             ('en', 'How to add fractions with different denominators?', 'resource-1'),
             ('fr', 'Comment additionner des fractions ?', 'resource-1'),
             ('mixed', 'video cach add fractions khac mau', 'resource-1'),
             ('plant', 'quang hợp ở thực vật', 'resource-2'),
             ('negative_subtract', 'phép trừ phân số', None),
             ('negative_respiration', 'hô hấp ở cây', None),
             ('no_answer', 'quantum cryptography', None)]
    methods = {}
    for method in ('bow', 'vector', 'hybrid'):
        rows = []
        for group, query, expected in cases:
            start = perf_counter()
            items, _, status, _ = service.retrieve(query, method, {}, SearchAccess('local_demo'),
                dictionary=c.search.dictionary, policy=c.settings.search_expansion)
            rows.append(dict(group=group, expected=expected, ids=[row['resource_id'] for row in items],
                             seconds=perf_counter() - start, branch_status=status))
        methods[method] = rows
    snapshots = service.snapshots({}, SearchAccess('local_demo'))
    versions = list(snapshots)
    queries = [model.embed_queries([query])[0]['vector'] for _, query, _ in cases]
    ablations = {}
    for label, fields in [('D0', ('title',)), ('D1', ('title', 'description')),
                          ('D2', ('title', 'description', 'tags')),
                          ('D3', ('title', 'description', 'tags', 'subject', 'grade'))]:
        texts = ['\n'.join(f'{field.title()}: {snapshots[key][1]["fields"][field]}'
                 for field in fields if field in snapshots[key][1]['fields']) for key in versions]
        vectors = model.embed_documents(texts)
        rows = []
        for (group, _, expected), query_vector in zip(cases, queries, strict=True):
            scores = {key: cosine(query_vector, vector['vector']) for key, vector in zip(versions, vectors, strict=True)}
            ids = [snapshots[key][0]['resource_id'] for key in sorted(scores, key=lambda key: (-scores[key], key))]
            rows.append(dict(group=group, expected=expected, ids=ids))
        ablations[label] = rows
    from data_sync_etl.domain.lexical import bm25
    from data_sync_etl.domain.metadata_search import lexical_fields
    for label, fields in [('L0', ('description',)), ('L1', ('title', 'description')), ('L2', ('title', 'description', 'tags'))]:
        rows = []
        for group, query, expected in cases:
            branches = [bm25(query, {key: lexical_fields(snapshots[key][1])[field] for key in versions},
                dictionary=c.search.dictionary, policy=c.settings.search_expansion) for field in fields]
            scores = rrf(*branches)
            rows.append(dict(group=group, expected=expected,
                ids=[snapshots[key][0]['resource_id'] for key in sorted(scores, key=lambda key: (-scores[key], key))]))
        ablations[label] = rows

    def metrics(rows):
        ranks = [row['ids'].index(row['expected']) + 1 if row['expected'] in row['ids'] else math.inf
                 for row in rows if row['expected']]
        negatives = [row for row in rows if row['expected'] is None]
        return {'P@1': sum(rank == 1 for rank in ranks) / len(ranks),
                'Recall@5': sum(rank <= 5 for rank in ranks) / len(ranks),
                'MRR@10': sum(1 / rank if rank <= 10 else 0 for rank in ranks) / len(ranks),
                'nDCG@10': sum(1 / math.log2(rank + 1) if rank <= 10 else 0 for rank in ranks) / len(ranks),
                'NoAnswerFPR': sum(bool(row['ids']) for row in negatives) / len(negatives)}
    import psutil
    memory = psutil.Process().memory_info()
    result = {'status': 'PASS real-inference component; quality DRAFT', 'checkpoint_revision': revision,
              'model_id': model.model_id, 'dimension': model.dimension, 'prepare_seconds': prepare_seconds,
              'index': full, 'corpus': '2 synthetic metadata-only registered versions', 'query_count': len(cases),
              'qrels': 'DRAFT developer assertions', 'methods': methods, 'ablations': ablations,
              'metrics': {key: metrics(rows) for key, rows in {**methods, **ablations}.items()},
              'rss_bytes': memory.rss, 'peak_rss_bytes': getattr(memory, 'peak_wset', None),
              'wall_seconds': perf_counter() - started, 'downloads': False, 'http': False,
              'encoder_counts': counts, 'resident_model_reused': model._model is resident,
              'content_dense_quality': 'NOT_RUN', 'L3': 'same lexical policy as L2; subject/grade are filters/context',
              'D4': 'not selected; no production strategy tuning'}
    destination = root / 'research/metadata-retrieval/model-current.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(len(row['vector']) == 1024 for row in service.store.rows.values())
    assert model._model is resident

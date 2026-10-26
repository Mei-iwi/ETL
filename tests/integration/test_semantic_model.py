"""Opt-in REAL offline BGE-M3 smoke; synthetic text, not thesis quality evidence.

Run in the semantic dependency environment with BGE_M3_TEST_PATH and
BGE_M3_TEST_REVISION. No database connection or schema installation is involved.
"""
import gc
import json
import math
import os
import platform
from pathlib import Path
from time import perf_counter, process_time

import pytest

from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.domain.evaluation import metrics, percentile
from data_sync_etl.domain.keywords import UnigramKeywords
from data_sync_etl.domain.search import cosine
from data_sync_etl.domain.semantic import embedding_input


@pytest.fixture(scope='module')
def real_model():
    path, revision = os.environ.get('BGE_M3_TEST_PATH'), os.environ.get('BGE_M3_TEST_REVISION')
    if not path or not revision:
        pytest.skip('Opt-in real offline model path/revision not configured')
    model = BgeM3Embedding(path, revision, max_tokens=512, batch_size=2, timeout_seconds=300)
    assert model.is_available(), 'Real FlagEmbedding/model must be provisioned; no test double fallback'
    model.prepare()
    return model


def test_real_dense_contextual_multilingual_and_colbert(real_model, tmp_path):
    import psutil
    import torch
    torch.set_num_threads(2)
    corpus = [
        ('fractions', 'Phép cộng phân số', 'Muốn cộng hai phân số khác mẫu số, tìm mẫu số chung, quy đồng rồi cộng các tử số và giữ nguyên mẫu số chung.'),
        ('plants', 'Quang hợp', 'Cây xanh hấp thụ năng lượng ánh sáng mặt trời, nước và khí carbon dioxide để tạo chất hữu cơ và giải phóng oxygen.'),
        ('security', 'Bảo vệ tài khoản', 'Trang web giả mạo có thể đánh cắp mật khẩu. Kiểm tra địa chỉ trang web và không nhập thông tin đăng nhập vào liên kết đáng ngờ.'),
        ('salt', 'Phương pháp tách chất', 'Để thu muối từ nước biển, làm bay hơi nước bằng cách đun nóng. Muối rắn còn lại sau khi nước bay hơi.'),
    ]
    queries = [
        ('Cách cộng phân số khác mẫu số', 'fractions'),
        ('How to add fractions with different denominators?', 'fractions'),
        ('Bài giảng fractions và quy đồng mẫu số', 'fractions'),
        ('How do plants make food using sunlight?', 'plants'),
        ('Nhận biết trang web đánh cắp mật khẩu', 'security'),
        ('How to separate salt from saltwater?', 'salt'),
    ]
    report = {'dataset': 'SYNTHETIC_SMOKE_ONLY_NOT_REAL_CORPUS',
              'model': real_model.model_identity(), 'platform': platform.platform(),
              'resources': len(corpus), 'queries': len(queries), 'methods': {}}
    ids = [row[0] for row in corpus]
    query_start = perf_counter()
    query_vectors = []
    for offset in range(0, len(queries), real_model.batch_size):
        query_vectors.extend(real_model.embed_queries([q[0] for q in queries[offset:offset + real_model.batch_size]]))
    report['query_embedding_seconds'] = perf_counter() - query_start
    for strategy in ('raw', 'contextual'):
        texts = [embedding_input({'text': text}, title=title, strategy=strategy)
                 for _, title, text in corpus]
        vectors, start, cpu_start = [], perf_counter(), process_time()
        for offset in range(0, len(texts), real_model.batch_size):
            vectors.extend(real_model.embed_documents(texts[offset:offset + real_model.batch_size]))
        build_seconds, build_cpu = perf_counter() - start, process_time() - cpu_start
        assert len(vectors) == len(corpus)
        assert all(len(row['vector']) == 1024 and math.hypot(*row['vector']) == pytest.approx(1.) for row in vectors)
        assert all(not row['truncated'] for row in vectors)
        for colbert in (False, True) if strategy == 'contextual' else (False,):
            observations, latencies, top1 = [], [], 0
            for (query, expected), output in zip(queries, query_vectors, strict=True):
                start = perf_counter()
                scores = {key: cosine(output['vector'], row['vector']) for key, row in zip(ids, vectors, strict=True)}
                ordered = sorted(ids, key=lambda key: (-scores[key], key))
                if colbert:
                    candidates = ordered[:20]
                    rerank = real_model.rerank(query, [texts[ids.index(key)] for key in candidates])
                    scores = dict(zip(candidates, rerank, strict=True))
                    ordered = sorted(candidates, key=lambda key: (-scores[key], key))
                latencies.append((perf_counter() - start) * 1000)
                top1 += ordered[0] == expected
                observations.append({
                    'p5_r5': metrics(ordered, {expected: 3}, 5),
                    'mrr10_ndcg10': metrics(ordered, {expected: 3}, 10)})
            name = strategy + ('+colbert' if colbert else '')
            report['methods'][name] = {
                'top1': top1, 'index_seconds': build_seconds, 'index_cpu_seconds': build_cpu,
                'rss_bytes': psutil.Process().memory_info().rss,
                'dense_float32_bytes': len(vectors) * 1024 * 4,
                'retrieval_p50_ms': percentile(latencies, .5), 'retrieval_p95_ms': percentile(latencies, .95),
                'latency_excludes_shared_query_encoding': True,
                'precision5': sum(row['p5_r5']['precision'] for row in observations) / len(queries),
                'recall5': sum(row['p5_r5']['recall'] for row in observations) / len(queries),
                'mrr10': sum(row['mrr10_ndcg10']['mrr'] for row in observations) / len(queries),
                'ndcg10': sum(row['mrr10_ndcg10']['ndcg'] for row in observations) / len(queries),
            }
            # A multilingual model smoke, not a general claim of improvement.
            assert top1 >= 4, report['methods'][name]
    # Real legacy keyword-vector baseline, one leaf/one bag per smoke resource.
    # Release FlagEmbedding first: do not retain two large models in this process.
    real_model._model = None
    gc.collect()
    legacy = LocalSentenceEmbedding(real_model.path, real_model.model_id,
                                   real_model.model_revision, 1024)
    legacy.prepare()
    start, cpu_start = perf_counter(), process_time()
    vectors = [legacy.embed(' | '.join(UnigramKeywords().extract(text))) for _, _, text in corpus]
    build_seconds, cpu_seconds = perf_counter() - start, process_time() - cpu_start
    start = perf_counter()
    encoded_queries = [legacy.embed(query) for query, _ in queries]
    legacy_query_seconds = perf_counter() - start
    latencies, observations, top1 = [], [], 0
    for (_, expected), vector in zip(queries, encoded_queries, strict=True):
        start = perf_counter()
        scores = {key: cosine(vector, doc) for key, doc in zip(ids, vectors, strict=True)}
        ordered = sorted(ids, key=lambda key: (-scores[key], key))
        latencies.append((perf_counter() - start) * 1000)
        top1 += ordered[0] == expected
        observations.append((metrics(ordered, {expected: 3}, 5), metrics(ordered, {expected: 3}, 10)))
    report['methods']['legacy'] = {
        'engine': 'LocalSentenceEmbedding', 'top1': top1, 'index_seconds': build_seconds,
        'index_cpu_seconds': cpu_seconds, 'query_embedding_seconds': legacy_query_seconds,
        'rss_bytes': psutil.Process().memory_info().rss, 'dense_float32_bytes': len(vectors) * 1024 * 4,
        'retrieval_p50_ms': percentile(latencies, .5), 'retrieval_p95_ms': percentile(latencies, .95),
        'latency_excludes_shared_query_encoding': True,
        'precision5': sum(row[0]['precision'] for row in observations) / len(queries),
        'recall5': sum(row[0]['recall'] for row in observations) / len(queries),
        'mrr10': sum(row[1]['mrr'] for row in observations) / len(queries),
        'ndcg10': sum(row[1]['ndcg'] for row in observations) / len(queries),
    }
    output_path = Path(os.environ.get('BGE_M3_TEST_REPORT', str(tmp_path / 'semantic-smoke.json')))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

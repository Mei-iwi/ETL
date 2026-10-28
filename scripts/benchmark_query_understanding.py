"""Offline lexical ablation on existing DRAFT qrels; no DB, model or tuning.

Semantic/Hybrid and HTTP quality remain NOT MEASURED. Fixture terminology is
not approved runtime data. All methods share the identical document corpus.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from types import ModuleType

from data_sync_etl.application.vector_quality import evaluate_ranking
from data_sync_etl.domain.concepts import ExpansionPolicy, empty_dictionary
from data_sync_etl.domain.evaluation import percentile
from data_sync_etl.domain.lexical import analysis_scope, bm25, lexical_document, surface_tokens
from data_sync_etl.domain.query_planner import lexical_plan_variants, plan_query
from data_sync_etl.domain.query_variants import approved_accent_variant
from data_sync_etl.domain.search import normalize_query

BASELINE = '9af41d889453f924bc3675c905f41498f2cd293c'
ARMS = ('reference_sha', 'A_original', 'B_core', 'C_accent', 'D_decomposition', 'E_multilingual', 'F_full_lexical')
EXAMPLES = (
    'quang hợp', 'quang hop', 'Hãy giải thích cho tôi biết quá trình quang hợp ở thực vật diễn ra như thế nào.',
    'Tôi cần tìm tài liệu hướng dẫn cách giải phương trình bậc hai.',
    'Không phải SQL mà là NoSQL', 'C++ khác C# như thế nào?', 'Tìm bài học về số âm',
    'So sánh CO2 và CO₂', 'How does photosynthesis work?', 'Comment fonctionne la photosynthèse ?',
    'Tìm tài liệu về machine learning', 'duong', 'cach cong hai phan so khac mau',
    'Tìm tài liệu về hệ quản trị cơ sở dữ liệu quan hệ, cách tổ chức bảng, khóa chính, khóa ngoại và mối liên hệ giữa các bảng.',
)


def load_baseline() -> ModuleType:
    source = subprocess.run(['git', 'show', f'{BASELINE}:src/data_sync_etl/domain/lexical.py'],
                            check=True, capture_output=True, encoding='utf-8').stdout
    module = ModuleType('_query_baseline_lexical')
    sys.modules[module.__name__] = module
    exec(compile(source, f'git:{BASELINE}/lexical.py', 'exec'), module.__dict__)
    return module


def group(row: dict) -> str:
    if not any(value >= 2 for value in row['qrels'].values()):
        return 'no_answer'
    if row['kind'] == 'accentless':
        return 'accentless'
    if row['kind'] == 'paraphrase':
        return 'paraphrase'
    if len(surface_tokens(row['text'])) >= 24:
        return 'long'
    return 'vietnamese' if row['language'] == 'vi' else 'multilingual'


def summarize(rows: list[dict]) -> dict:
    positive = [row for row in rows if row['metrics']['has_positive']]
    negative = [row for row in rows if not row['metrics']['has_positive']]
    result = {'queries': len(rows)}
    for key in ('precision1', 'precision5', 'recall5', 'recall10', 'mrr10', 'ndcg10', 'hit5'):
        values = [row['metrics'][key] for row in positive if row['metrics'][key] is not None]
        result[key] = sum(values) / len(values) if values else None
    result['no_answer_fpr'] = sum(not row['metrics']['abstained'] for row in negative) / len(negative) if negative else None
    result['false_abstention_rate'] = sum(row['metrics']['abstained'] for row in positive) / len(positive) if positive else None
    for key in ('processing_ms', 'lexical_search_ms'):
        result[key] = {'p50': percentile([row[key] for row in rows], .5),
                       'p95': percentile([row[key] for row in rows], .95)} if rows else None
    return result


def run(output: Path) -> dict:
    fixture = json.loads(Path('fixtures/search/multilingual_context.json').read_text(encoding='utf-8'))
    extra = json.loads(Path('fixtures/search/phase2_queries.json').read_text(encoding='utf-8'))
    reference = load_baseline()
    documents = {doc['id']: lexical_document([doc['text']], title=doc['title']) for doc in fixture['documents']}
    dictionary, policy = empty_dictionary(), ExpansionPolicy()

    def retrieve(query: str, arm: str) -> tuple[list[str], list[str], float, float]:
        start = perf_counter()
        plan = None if arm in ('reference_sha', 'A_original', 'C_accent') else plan_query(query)
        variants = [normalize_query(query)]
        if plan and arm in ('B_core', 'D_decomposition', 'F_full_lexical'):
            variants = list(lexical_plan_variants(plan, decompose=arm in ('D_decomposition', 'F_full_lexical')))
        if arm in ('C_accent', 'F_full_lexical'):
            accent = approved_accent_variant(query, dictionary, policy=policy)
            if accent:
                variants = list(dict.fromkeys([variants[0], accent, *variants[1:]]))[:4]
        processing = (perf_counter() - start) * 1000
        scores = {}
        for index, text in enumerate(variants):
            scorer = reference.bm25 if arm == 'reference_sha' else bm25
            branch = scorer(text, documents, dictionary=dictionary, policy=policy,
                            accent_insensitive=arm in ('C_accent', 'F_full_lexical'))
            for key, value in branch.items():
                scores[key] = max(scores.get(key, 0.), value * (1. if index == 0 else policy.synonyms))
        return sorted(scores, key=lambda key: (-scores[key], key)), variants, processing, (perf_counter() - start) * 1000

    rows = []
    for row in [*fixture['queries'], *extra['queries']]:
        for arm in ARMS:
            with analysis_scope():
                order, variants, processing, elapsed = retrieve(row['text'], arm)
            metrics = evaluate_ranking(order, row['qrels'])
            positives = {key for key, value in row['qrels'].items() if value >= 2}
            metrics['recall10'] = len(set(order[:10]) & positives) / len(positives) if positives else None
            rows.append({'id': row['id'], 'group': group(row), 'arm': arm, 'metrics': metrics,
                         'variants': variants, 'top10': order[:10], 'processing_ms': processing,
                         'lexical_search_ms': elapsed})
    examples = []
    for query in EXAMPLES:
        with analysis_scope():
            before = retrieve(query, 'reference_sha')[0]
            after, variants, *_ = retrieve(query, 'F_full_lexical')
        examples.append({'query': query, 'plan': plan_query(query).model_dump(mode='json'),
                         'method': 'offline BoW; no semantic model', 'variants': variants,
                         'before_top5': before[:5], 'after_top5': after[:5], 'quality': 'UNJUDGED_EXAMPLE'})
    report = {'status': 'DRAFT_FIXTURE_DIAGNOSTIC_NOT_INDEPENDENT_ACCEPTANCE', 'baseline_sha': BASELINE,
        'working_head': subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip(),
        'qrels_sources': ['fixtures/search/multilingual_context.json', 'fixtures/search/phase2_queries.json'],
        'label_status': [fixture['label_status'], extra['label_status']],
        'approved_dictionary': 'empty; no fixture concepts promoted to runtime approvals',
        'accent_arm': 'existing opt-in authorized-corpus restoration; not semantic translation',
        'not_measured': ['live corpus quality', 'BGE-M3 quality/latency', 'Hybrid quality', 'HTTP/database latency',
                         'production concurrent throughput', 'independent holdout acceptance'],
        'summary': {arm: summarize([row for row in rows if row['arm'] == arm]) for arm in ARMS},
        'groups': {name: {arm: summarize([row for row in rows if row['arm'] == arm and row['group'] == name])
                         for arm in ARMS} for name in ('vietnamese', 'accentless', 'long', 'multilingual', 'paraphrase', 'no_answer')},
        'rows': rows, 'examples': examples}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.test-tmp/query-understanding/report.json'))
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        parser.error('Output must stay inside .test-tmp')
    report = run(args.output)
    print(json.dumps({'status': report['status'], 'summary': report['summary']}, ensure_ascii=False))

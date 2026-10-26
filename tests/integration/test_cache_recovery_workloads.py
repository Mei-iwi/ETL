"""Opt-in recovery workload: owned PG/Mongo, real384D, no Dev/production writes."""
import ast
import hashlib
import inspect
import json
import os
import random
import sys
import textwrap
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter, process_time

import pytest
from pymongo import MongoClient, monitoring
from sqlalchemy import event

import data_sync_etl.application.search as search_module
from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.application.http_query_cache import COUNTERS
from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.conftest import make_pdf
from tests.integration.test_http_query_cache_pg import memory, summary
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_CACHE_RECOVERY') != '1', reason='Explicit owned physical recovery benchmark only')]
ACCESS = SearchAccess('local_demo')
PHASES = ('query_encode', 'readiness_probe', 'model_prepare', 'model_ready',
          'postgres_roundtrip', 'mongo_roundtrip', 'scoring', 'ranking_output')


@pytest.fixture
def container(pg_container):
    return pg_container


class Profiler:
    def __init__(self):
        self.active = False
        self.stack, self.ms, self.counts = [], Counter(), Counter()

    @contextmanager
    def span(self, name):
        if not self.active:
            yield
            return
        frame = [perf_counter(), 0.]
        self.stack.append(frame)
        try:
            yield
        finally:
            elapsed = perf_counter() - frame[0]
            assert self.stack.pop() is frame
            if self.stack:
                self.stack[-1][1] += elapsed
            self.ms[name] += (elapsed - frame[1]) * 1000
            self.counts[name] += 1

    def reset(self):
        assert not self.stack
        self.ms.clear()
        self.counts.clear()

    def wrapper(self, function, name):
        def call(*args, **kwargs):
            category = name(*args, **kwargs) if callable(name) else name
            with self.span(category):
                return function(*args, **kwargs)
        return call


def ranking_trace(profile):
    # Derive ranges from THIS HEAD, never assumed line numbers or patched ranking.
    lines, offset = inspect.getsourcelines(SearchService.search)
    tree = ast.parse(textwrap.dedent(''.join(lines))).body[0]
    start = next(node.lineno for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'scores' for target in node.targets))
    check = next(node.lineno for node in tree.body if isinstance(node, ast.If)
                 and isinstance(node.test, ast.Name) and node.test.id == 'grouped')
    tail = next(node.lineno for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'items' for target in node.targets))
    selected = {offset + line - 1 for line in (*range(start, check), *range(tail, tree.end_lineno + 1))}
    def trace(frame, event, _arg):
        if frame.f_code is not SearchService.search.__code__:
            return None
        stamp, previous = perf_counter(), None
        def local(_frame, current, _arg):
            nonlocal stamp, previous
            value = perf_counter()
            if previous in selected:
                profile.ms['ranking_output'] += (value - stamp) * 1000
            stamp, previous = value, _frame.f_lineno if current == 'line' else None
            return local
        return local
    return trace


def workload(size, repeat_fraction):
    topics = ['phân số', 'phương trình', 'hình tam giác', 'diện tích hình tròn', 'số nguyên',
              'tỉ lệ thức', 'chuyển động', 'lực ma sát', 'định luật Newton', 'điện năng',
              'ánh sáng', 'nhiệt độ', 'nguyên tử', 'phản ứng hóa học', 'dung dịch',
              'quang hợp', 'tế bào', 'hệ sinh thái', 'cơ thể người', 'ô nhiễm môi trường',
              'lịch sử Việt Nam', 'địa lí tự nhiên', 'ngữ pháp tiếng Việt', 'văn nghị luận', 'đọc hiểu']
    forms = ['Giải thích {} bằng ví dụ', 'Bài tập về {} và cách giải',
             'Tìm tài liệu học về {}', 'Kiến thức cơ bản của {}']
    pool = [form.format(topic) for form in forms for topic in topics]
    random.Random(731).shuffle(pool)
    unique = max(1, round(size * (1 - repeat_fraction)))
    queries = pool[:unique] + [pool[index % unique] for index in range(size - unique)]
    return queries, unique


def test_recovery_workloads(searchable, mongo_test_database, monkeypatch):
    import sentence_transformers
    import torch

    torch.set_num_threads(2)
    c, first = searchable
    profile = Profiler()
    loads = []
    construct = sentence_transformers.SentenceTransformer
    def constructor(*args, **kwargs):
        loads.append(1)
        return construct(*args, **kwargs)
    monkeypatch.setattr(sentence_transformers, 'SentenceTransformer', constructor)
    for name, category in (('prepare', 'model_prepare'), ('ready', 'model_ready'),
                           ('_encode', lambda self, _text: 'query_encode' if self._checked else 'readiness_probe')):
        monkeypatch.setattr(LocalSentenceEmbedding, name, profile.wrapper(getattr(LocalSentenceEmbedding, name), category))
    for name in ('binary_score', 'cosine'):
        monkeypatch.setattr(search_module, name, profile.wrapper(getattr(search_module, name), 'scoring'))
    model_path = Path(os.environ['ETL_QUERY_CACHE_MODEL_PATH']).resolve()
    revision = (model_path / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    assert revision == 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
    model = LocalSentenceEmbedding(model_path, 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', revision, 384)
    rss_before_model = memory()
    profile.active = True
    started = perf_counter()
    model.prepare()
    cold = {'wall_ms': (perf_counter() - started) * 1000, 'exclusive_phase_ms': dict(profile.ms),
            'counts': dict(profile.counts), 'rss_before': rss_before_model, 'rss_after': memory()}
    profile.active = False
    # Small physical corpus, not a claim of representative Dev/production corpus size.
    with c.uow() as repo:
        resource = repo.get('master_learning_resources', first['resource_id'])
        repo.insert('master_learning_resources', {**resource, 'id': 'recovery-resource-2', 'mongo_id': None, 'source_origin': 'LOCAL'})
    path = make_pdf(c, ['Page fractions addition mathematics learning.', 'Page motion force physics teaching.'], name='recovery.pdf')
    second = c.ingestion.run('recovery-resource-2', 'input', path.name)['version']
    c.ocr.create_job(second['id'])
    while c.worker.once('isolated-cache-recovery'):
        pass

    class Commands(monitoring.CommandListener):
        def started(self, _event):
            pass
        def succeeded(self, event):
            if profile.active and event.command_name in ('find', 'listCollections', 'listIndexes', 'getMore'):
                profile.ms['mongo_roundtrip'] += event.duration_micros / 1000
                profile.counts['mongo_roundtrip'] += 1
        def failed(self, _event):
            pass

    with MongoClient(mongo_test_database[0], tz_aware=True, event_listeners=[Commands()],
                     serverSelectionTimeoutMS=3000, timeoutMS=10000) as client:
        database = client[mongo_test_database[1]]
        assert database.name.startswith('etl_test_')
        assert initialize(database, apply=True, dimension=384)['ready']
        database.mongo_content_units.insert_many(list(c.documents.units.values()))
        c.indexing.reader, c.indexing.bags = MongoContentReader(database), MongoBagStore(database, 384)
        c.indexing.embedding = model
        c.settings.index_embedding_dimension = 384
        for version in (first, second):
            c.indexing.enqueue(version['id'], force=True)
            assert c.indexing.once()
        services = {name: SearchService(c.indexing, c.settings.model_copy(update={
            'search_query_embedding_cache_enabled': name == 'on'}), c.indexing.bags, query_cache_runtime=model)
                    for name in ('off', 'on')}
        def before(conn, _cursor, statement, _params, _ctx, _many):
            if profile.active and statement.lstrip().upper().startswith('SELECT'):
                conn.info['recovery_select_started'] = perf_counter()
        def after(conn, *_args):
            stamp = conn.info.pop('recovery_select_started', None)
            if stamp is not None:
                profile.ms['postgres_roundtrip'] += (perf_counter() - stamp) * 1000
                profile.counts['postgres_roundtrip'] += 1
        event.listen(c.engine, 'before_cursor_execute', before)
        event.listen(c.engine, 'after_cursor_execute', after)
        target = Path(os.environ['ETL_CACHE_RECOVERY_METRICS']).resolve()
        assert target.is_relative_to(Path.cwd().resolve() / '.test-tmp/audits/search-query-vector-cache')
        report = {
            'head': '358cf921125df28a95edd3fbaa72829c0c771eae', 'status': 'RUNNING',
            'scope': 'isolated real PG/Mongo + MiniLM384D; 2 resources; SearchService/admission, not HTTP or Dev',
            'corpus_resources': 2, 'cold_n': 1, 'cold': cold,
            'model': {'id': model.model_id, 'revision': revision, 'dimension': 384},
            'profiling': 'exclusive nested model spans; DB command/cursor roundtrip only, hydration excluded; pure scoring; AST-derived grouping/RRF/final sorting/output ranges; residual separately reported',
            'workload_policy': '30/50/75/100 requests per side/method, requested repeat fractions 0/0.5/0.9; exact unique counts and observed hit rates published',
            'groups': [], 'production_source_sha256': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
                for p in ('src/data_sync_etl/application/http_query_cache.py', 'src/data_sync_etl/application/search.py')},
        }
        def save():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        save()
        old_trace = sys.gettrace()
        assert old_trace is None
        trace = ranking_trace(profile)
        smoke = os.environ.get('ETL_CACHE_RECOVERY_SMOKE') == '1'
        positive = os.environ.get('ETL_CACHE_RECOVERY_POSITIVE') == '1'
        report['positive_lexical_control'] = positive
        if positive:
            report['workload_policy'] = '3 lexical-positive control groups,30 requests/side/method,90% repeats; repeated Page anchor is verified in current top5 query keywords, not a relevance label'
        expected_groups = 3 if positive else 1 if smoke else 36
        try:
            for size in ((30,) if smoke or positive else (30, 50, 75, 100)):
                for fraction in ((.9,) if smoke or positive else (0., .5, .9)):
                    queries, unique = workload(size, fraction)
                    if positive:
                        queries = ['Page Page Page: ' + query for query in queries]  # Explicit top5 anchor, NOT relevance labels.
                        assert all('page' in c.indexing.extractor.extract(query) for query in queries)
                    for method in (('vector',) if smoke else ('bow', 'vector', 'hybrid')):
                        for service in services.values():
                            service._http_query_vectors.clear()
                        stats_before = services['on']._http_query_vectors.stats()
                        samples = {label: [] for label in services}
                        phase_samples = {label: {phase: [] for phase in (*PHASES, 'unattributed')} for label in services}
                        counts, cpu, errors = {label: Counter() for label in services}, Counter(), Counter()
                        rss_before = memory()
                        signatures = []
                        for index, query in enumerate(queries):
                            results = {}
                            for label in (('off', 'on') if index % 2 == 0 else ('on', 'off')):
                                profile.reset()
                                started, cpu_started = perf_counter(), process_time()
                                profile.active = True
                                sys.settrace(trace)
                                try:
                                    with services[label].runtime_admission(method, access=ACCESS):
                                        results[label] = services[label].search_http(query, method, page=(1, 2, 1, 3)[index % 4],
                                                                                   size=(5, 10, 20)[index % 3], access=ACCESS)
                                except (SearchError, IndexingError) as exc:
                                    results[label] = {'safe_error': exc.code, 'status': exc.status}
                                    errors[label] += 1
                                finally:
                                    sys.settrace(old_trace)
                                    profile.active = False
                                elapsed = (perf_counter() - started) * 1000
                                cpu[label] += process_time() - cpu_started
                                samples[label].append(elapsed)
                                counts[label].update(profile.counts)
                                if positive:
                                    counts[label]['queries_with_matches'] += int(results[label].get('total', 0) > 0)
                                    if method == 'bow':
                                        assert results[label].get('total') == 2
                                for phase in PHASES:
                                    phase_samples[label][phase].append(profile.ms[phase])
                                phase_samples[label]['unattributed'].append(elapsed - sum(profile.ms.values()))
                                assert services[label]._http_query_vectors.stats()['accounted_bytes'] <= 1048576
                            assert results['off'] == results['on']
                            signatures.append(hashlib.sha256(json.dumps(results['off'], sort_keys=True).encode()).hexdigest())
                        stats = services['on']._http_query_vectors.stats()
                        group = {
                            'requests_per_side': size, 'requested_repeat_fraction': fraction, 'unique_queries': unique,
                            'actual_repeat_fraction': (size - unique) / size, 'method': method,
                            'latency_ms': {label: summary(values) for label, values in samples.items()},
                            'exclusive_phase_ms': {label: {phase: summary(values) for phase, values in phases.items()}
                                                   for label, phases in phase_samples.items()},
                            'counts': {label: dict(value) for label, value in counts.items()}, 'cpu_seconds': dict(cpu),
                            'errors': {label: errors[label] for label in services},
                            'error_rate': {label: errors[label] / size for label in services},
                            'cache_delta': {key: stats[key] - stats_before[key] for key in COUNTERS},
                            'cache_end': stats, 'rss_before': rss_before, 'rss_after': memory(),
                            'exact_response_parity': True, 'ordered_response_hash': hashlib.sha256(''.join(signatures).encode()).hexdigest(),
                            'paired_delta_ms': summary([off - on for off, on in zip(samples['off'], samples['on'], strict=True)]),
                            'on_faster_pairs': sum(off > on for off, on in zip(samples['off'], samples['on'], strict=True)),
                        }
                        report['groups'].append(group)
                        save()
                        print('RECOVERY_GROUP ' + str(len(report['groups'])) + '/' + str(expected_groups) + ' n=' + str(size) +
                              ' repeat=' + str(fraction) + ' method=' + method, flush=True)
                        assert not errors['off'] and not errors['on']
                        assert counts['off']['postgres_roundtrip'] == counts['on']['postgres_roundtrip']
                        assert counts['off']['mongo_roundtrip'] == counts['on']['mongo_roundtrip']
            assert len(loads) == 1 and len(report['groups']) == expected_groups
            report.update(status='CONTROL_PASS' if positive else 'SMOKE_PASS' if smoke else 'PASS', model_loads=len(loads),
                          pairs=sum(g['requests_per_side'] for g in report['groups']))
            save()
        finally:
            sys.settrace(old_trace)
            profile.active = False
            event.remove(c.engine, 'before_cursor_execute', before)
            event.remove(c.engine, 'after_cursor_execute', after)

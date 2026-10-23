"""Bounded synchronous benchmarks over the existing SearchService, not a second ranker."""
import csv
import inspect
import io
import platform
from contextlib import closing
from datetime import datetime
from time import perf_counter

from data_sync_etl.application.content_bags import bag_structure
from data_sync_etl.application.search import QueryEmbeddingContext
from data_sync_etl.domain.core import new_id, now
from data_sync_etl.domain.evaluation import (
    METHODS,
    EvaluationError,
    aggregate,
    checksum,
    csv_cell,
    metrics,
)
from data_sync_etl.domain.search import binary_score, cosine, rrf
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.ports.evaluation import EvaluationStorePort


class EvaluationService:
    def __init__(self, search, store: EvaluationStorePort, settings):
        self.search, self.store, self.settings = search, store, settings

    @staticmethod
    def authorized(access):
        if not access.internal:
            raise EvaluationError('EVALUATION_FORBIDDEN', 403)

    def corpus(self, filters, access):
        self.authorized(access)
        search, limit = self.search, self.settings.evaluation_max_corpus_versions
        if search.bags is None:
            raise EvaluationError('EVALUATION_NO_READY_CORPUS', 409)
        search.bags.ensure_ready()
        with search.indexing.uow() as repo:
            rows = search._metadata(repo, filters, access, limit + 1)
        if len(rows) > limit:
            raise EvaluationError('EVALUATION_CORPUS_LIMIT')
        versions = []
        for row in rows:
            with search.indexing.uow() as repo:
                snapshot = search._ready_snapshot(repo, row['version_id'])
                if snapshot is None:
                    continue
                allowed = search._allowed_units(repo, snapshot, access)
                bags = []
                for root, leaves in sorted(bag_structure(snapshot[2]).items()):
                    if not set(leaves) <= allowed:
                        continue
                    bag = search.bags.get_bag(root)
                    if bag is not None:
                        bags.append({'id': root, 'hash': checksum(bag.model_dump(mode='json'))})
                if bags:
                    versions.append({'resource_id': row['resource_id'], 'version_id': row['version_id'],
                                     'generation': snapshot[1], 'index_fingerprint': snapshot[3],
                                     'index_run_id': search.indexing._runs(repo, row['version_id'])[0]['id'],
                                     'metadata_hash': checksum(row), 'allowed_units_hash': checksum(sorted(allowed)),
                                     'bags': bags})
                    if self.settings.semantic_enabled:
                        if search.semantic is None:
                            raise EvaluationError('SEMANTIC_SCHEMA_NOT_READY', 503)
                        plan, _ = search.semantic.plan(snapshot, repo.semantic_metadata(row['resource_id']))
                        manifest = search.semantic.store.manifest(plan['_id'])
                        if not manifest or manifest['status'] != 'READY':
                            raise EvaluationError('SEMANTIC_INDEX_NOT_READY', 503)
                        versions[-1]['semantic_index_id'] = plan['_id']
        if not versions:
            raise EvaluationError('EVALUATION_NO_READY_CORPUS', 409)
        model = search.indexing.embedding
        snapshot = {'scope': access.scope, 'relevance_unit': 'resource', 'versions': versions,
                    'model': {'id': model.model_id, 'version': model.version, 'dimension': model.dimension},
                    'search_version': checksum([inspect.getsource(type(search)), inspect.getsource(binary_score),
                                                inspect.getsource(cosine), inspect.getsource(rrf)]),
                    'policy_version': checksum(inspect.getsource(SearchAccess)),
                    'filters': filters, 'search_max_bags': self.settings.search_max_bags,
                    'search_timeout_seconds': self.settings.search_timeout_seconds}
        if self.settings.semantic_enabled:
            semantic = search.semantic.embedding
            snapshot['model'] = {'id': semantic.model_identity()['model_id'],
                'version': semantic.model_revision, 'dimension': semantic.dimension}
            snapshot['semantic'] = {'identity': semantic.model_identity(),
                'strategy': self.settings.semantic_strategy,
                'rerank_enabled': self.settings.semantic_rerank_enabled,
                'rerank_top_k': self.settings.semantic_rerank_top_k,
                'min_score': self.settings.semantic_min_score,
                'max_candidates': self.settings.semantic_max_candidates}
        return snapshot, checksum(snapshot)

    @staticmethod
    def label_ids(dataset):
        return {label['item_id'] for query in dataset['queries'] for label in query['relevance']}

    def create_dataset(self, command, access):
        self.authorized(access)
        if len(command['queries']) > self.settings.evaluation_max_queries:
            raise EvaluationError('EVALUATION_SIZE_LIMIT')
        snapshot, fingerprint = self.corpus(command['filters'], access)
        ids = {row['resource_id'] for row in snapshot['versions']}
        if not self.label_ids(command) <= ids:
            raise EvaluationError('EVALUATION_QRELS_DENIED', 403)
        dataset = {**command, 'id': new_id(), 'version': 1, 'created_at': now().isoformat(),
                   'relevance_threshold': 1, 'scope': access.scope, 'corpus_snapshot': snapshot,
                   'corpus_fingerprint': fingerprint, 'dataset_hash': checksum(command), 'auth_user_id': access.actor_id}
        self.store.put('datasets', dataset)
        positives = sum(label['grade'] >= 1 for query in command['queries'] for label in query['relevance'])
        return {'dataset_id': dataset['id'], 'version': 1, 'name': dataset['name'],
                'number_of_queries': len(command['queries']), 'relevant_items': positives,
                'relevance_unit': 'resource', 'relevance_threshold': 1,
                'dataset_hash': dataset['dataset_hash'], 'created_at': dataset['created_at'],
                'validation': {'ground_truth_confirmed': dataset.get('ground_truth_confirmed', False),
                               'warnings': self.warnings(dataset)}}

    @staticmethod
    def warnings(dataset):
        result = ['UNJUDGED_ITEMS_TREATED_AS_GRADE_0', 'LOCAL_DEMO_NOT_PRODUCTION_AUTH']
        if not dataset.get('ground_truth_confirmed', False):
            result.append('LABELS_NOT_HUMAN_CONFIRMED')
        if len(dataset['queries']) < 10:
            result.append('SMALL_DATASET_NO_GENERAL_CONCLUSION')
        if any(not any(label['grade'] >= 1 for label in query['relevance']) for query in dataset['queries']):
            result.append('QUERIES_WITHOUT_POSITIVE_QRELS_EXCLUDED_FROM_RECALL_MRR_NDCG')
        return result

    def run(self, command, access):
        with closing(self.run_events(command, access)) as events:
            for event in events:
                if event['type'] == 'result':
                    return event['run']

    def run_events(self, command, access):
        self.authorized(access)
        with self.store.lock('run'):
            dataset = self.store.get('datasets', command['dataset_id'])
            if len(dataset['queries']) > self.settings.evaluation_max_queries:
                raise EvaluationError('EVALUATION_SIZE_LIMIT')
            marker = self.corpus_marker(dataset['filters'], access)
            snapshot, fingerprint = self.corpus(dataset['filters'], access)
            if dataset['scope'] != access.scope or dataset.get('auth_user_id') != access.actor_id:
                raise EvaluationError('EVALUATION_FORBIDDEN', 403)
            if fingerprint != dataset['corpus_fingerprint']:
                raise EvaluationError('EVALUATION_DATASET_STALE', 409)
            self.check_marker(dataset, access, marker)
            run = {'id': new_id(), 'dataset_id': dataset['id'], 'dataset_name': dataset['name'],
                   'dataset_hash': dataset['dataset_hash'], 'schema_version': 1, 'scope': access.scope,
                   'status': 'RUNNING', 'created_at': now().isoformat(), 'finished_at': None, 'auth_user_id': access.actor_id,
                   'params': command, 'snapshot': snapshot, 'corpus_fingerprint': fingerprint,
                   'queries': dataset['queries'], 'relevance_unit': 'resource', 'relevance_threshold': 1,
                   'warnings': self.warnings(dataset), 'observations': [], 'summary': {},
                   'measurement': {'latency_scope': 'whole SearchService call; excludes corpus validation',
                                   'warmup_queries_per_method': command['warmup_queries'],
                                   'cold_start_included_unless_warmed': True,
                                   'method_schedule': 'rotate bow/vector/hybrid by query index',
                                   'aggregation': 'macro per metric over successful queries with defined values',
                                   'precision_denominator': 'k, including missing results',
                                   'mrr_cutoff': 'k', 'ndcg_gain': '2^grade-1',
                                   'percentile': 'linear interpolation',
                                   'corpus_validation': 'full before/after run; batched PG marker before/after each search; Search guards unchanged',
                                   'timeout_seconds': self.settings.evaluation_run_timeout_seconds,
                                   'python': platform.python_version(), 'platform': platform.system()}}
            self.store.put('runs', run)
            total = (len(dataset['queries']) + command['warmup_queries']) * len(METHODS)
            completed = 0
            def progress(phase):
                return {'type': 'progress', 'run_id': run['id'], 'completed': completed, 'total': total,
                        'percent': min(99, completed * 100 // total), 'phase': phase}
            started = perf_counter()
            try:
                yield progress('preparing')
                for index in range(command['warmup_queries']):
                    for method in METHODS:
                        self.deadline(started)
                        self.check_marker(dataset, access, marker)
                        self.search.search(dataset['queries'][index % len(dataset['queries'])]['query'], method,
                                           filters=dataset['filters'], size=command['max_results'], access=access)
                        self.check_marker(dataset, access, marker)
                        self.deadline(started)
                        completed += 1
                        yield progress('warmup')
                for qi, query in enumerate(dataset['queries']):
                    qrels = {label['item_id']: label['grade'] for label in query['relevance']}
                    order = METHODS[qi % 3:] + METHODS[:qi % 3]
                    with QueryEmbeddingContext() as query_context:
                        for method in order:
                            self.deadline(started)
                            self.check_marker(dataset, access, marker)
                            call_start = perf_counter()
                            row = {'query_id': query['query_id'], 'method': method, 'ranked': [],
                                   'metrics': {}, 'latency_ms': None, 'error_code': None}
                            try:
                                response = self.search.search(query['query'], method, filters=dataset['filters'],
                                                              size=command['max_results'], access=access,
                                                              _query_context=query_context)
                                row['latency_ms'] = (perf_counter() - call_start) * 1000
                                seen = set()
                                for item in response['items']:
                                    if item['resource_id'] not in seen:
                                        seen.add(item['resource_id'])
                                        row['ranked'].append({key: item[key] for key in (
                                            'resource_id', 'resource_version_id', 'bag_id', 'score', 'score_components')})
                                row['metrics'] = {str(k): metrics([item['resource_id'] for item in row['ranked']], qrels, k)
                                                  for k in command['k_values']}
                            except Exception as exc:
                                query_context.clear()
                                row['latency_ms'] = (perf_counter() - call_start) * 1000
                                row['error_code'] = self.safe_dependency_code(exc)
                            run['observations'].append(row)
                            self.check_marker(dataset, access, marker)
                            self.deadline(started)
                            completed += 1
                            yield progress('running')
                self.check_corpus(dataset, access, fingerprint)
                self.check_marker(dataset, access, marker)
                self.deadline(started)
                errors = sum(row['error_code'] is not None for row in run['observations'])
                run['status'] = 'COMPLETED' if not errors else 'FAILED' if errors == len(run['observations']) else 'PARTIAL'
            except GeneratorExit:
                run['status'] = 'FAILED'
                run['warnings'].append('EVALUATION_CANCELLED')
                run['finished_at'] = now().isoformat()
                run['duration_ms'] = (perf_counter() - started) * 1000
                self.store.put('runs', run, replace=True)
                raise
            except Exception as exc:
                code = self.safe_dependency_code(exc)
                run['status'] = 'NOT_COMPARABLE' if code in ('EVALUATION_CORPUS_CHANGED', 'EVALUATION_QRELS_DENIED') else 'FAILED'
                run['warnings'].append(code)
            run['summary'] = aggregate(run['observations'], command['k_values']) if run['status'] in ('COMPLETED', 'PARTIAL') else {}
            if run['status'] == 'NOT_COMPARABLE':
                for observation in run['observations']:
                    observation['metrics'] = {}
            run['finished_at'], run['duration_ms'] = now().isoformat(), (perf_counter() - started) * 1000
            self.store.put('runs', run, replace=True)
            yield {'type': 'result', 'run': run, 'completed': completed, 'total': total,
                   'percent': 100 if run['status'] == 'COMPLETED' else min(99, completed * 100 // total)}

    @staticmethod
    def safe_dependency_code(exc):
        code = getattr(exc, 'code', None)
        return code if isinstance(code, str) and code.isascii() and code.replace('_', '').isalnum() and len(code) <= 80 else 'EVALUATION_DEPENDENCY_UNAVAILABLE'

    def deadline(self, started):
        if perf_counter() - started > self.settings.evaluation_run_timeout_seconds:
            raise EvaluationError('EVALUATION_TIMEOUT', 503)

    def check_corpus(self, dataset, access, expected):
        try:
            _, actual = self.corpus(dataset['filters'], access)
        except Exception:
            raise EvaluationError('EVALUATION_CORPUS_CHANGED', 409) from None
        if actual != expected:
            raise EvaluationError('EVALUATION_CORPUS_CHANGED', 409)

    def corpus_marker(self, filters, access):
        self.authorized(access)
        with self.search.indexing.uow() as repo:
            marker = repo.evaluation_state(filters, limit=self.settings.evaluation_max_corpus_versions,
                                           max_units=self.settings.index_max_units)
        model = self.search.indexing.embedding
        semantic = ([self.search.semantic.embedding.model_identity(), self.settings.semantic_strategy,
                     self.settings.semantic_rerank_enabled, self.settings.semantic_rerank_top_k,
                     self.settings.semantic_min_score, self.settings.semantic_max_candidates]
                    if self.settings.semantic_enabled and self.search.semantic is not None else None)
        return checksum([marker, access.scope, model.model_id, model.version, model.dimension, semantic,
                         self.settings.chunk_size_words, self.settings.chunk_overlap_words,
                         self.settings.search_max_bags, self.settings.search_timeout_seconds])

    def check_marker(self, dataset, access, expected):
        try:
            actual = self.corpus_marker(dataset['filters'], access)
        except Exception:
            raise EvaluationError('EVALUATION_CORPUS_CHANGED', 409) from None
        if actual != expected:
            raise EvaluationError('EVALUATION_CORPUS_CHANGED', 409)

    def list_runs(self, access, page=1, size=7):
        self.authorized(access)
        snapshots, rows = {}, []
        for run in self.store.list_runs():
            if run['scope'] != access.scope or run.get('auth_user_id') != access.actor_id:
                continue
            filters = run['snapshot']['filters']
            key = checksum(filters)
            if key not in snapshots:
                try:
                    snapshots[key], _ = self.corpus(filters, access)
                except EvaluationError as exc:
                    if exc.code != 'EVALUATION_NO_READY_CORPUS':
                        raise
                    snapshots[key] = {'versions': []}
            try:
                self.check_run_access(run, access, snapshots[key])
            except EvaluationError as exc:
                if exc.code not in ('EVALUATION_FORBIDDEN', 'EVALUATION_QRELS_DENIED'):
                    raise
                continue
            rows.append({key: run.get(key) for key in (
                'id', 'dataset_id', 'dataset_name', 'status', 'created_at', 'finished_at', 'duration_ms'
            )} | {'query_count': len(run['queries'])})
        rows.sort(key=lambda row: (datetime.fromisoformat(row['created_at']).timestamp(), row['id']), reverse=True)
        return {'items': rows[(page - 1) * size:page * size],
                'page': page, 'size': size, 'total': len(rows),
                'total_pages': max(1, (len(rows) + size - 1) // size)}

    @staticmethod
    def check_run_access(run, access, snapshot):
        if run['scope'] != access.scope or run.get('auth_user_id') != access.actor_id:
            raise EvaluationError('EVALUATION_FORBIDDEN', 403)
        allowed = {row['resource_id'] for row in snapshot['versions']}
        referenced = {label['item_id'] for query in run['queries'] for label in query['relevance']}
        referenced.update(item['resource_id'] for row in run['observations'] for item in row['ranked'])
        if not referenced <= allowed:
            raise EvaluationError('EVALUATION_QRELS_DENIED', 403)

    def get_run(self, identifier, access):
        self.authorized(access)
        run = self.store.get('runs', identifier)
        if run['scope'] != access.scope or run.get('auth_user_id') != access.actor_id:
            raise EvaluationError('EVALUATION_FORBIDDEN', 403)
        # GET never runs Search or embeds a query. Recheck current read permissions.
        snapshot, _ = self.corpus(run['snapshot']['filters'], access)
        self.check_run_access(run, access, snapshot)
        return run

    def report(self, identifier, access, format='csv'):
        run = self.get_run(identifier, access)
        if run['status'] != 'COMPLETED':
            raise EvaluationError('EVALUATION_REPORT_INCOMPLETE', 409)
        if format == 'json':
            return {key: value for key, value in run.items() if key != 'auth_user_id'}
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(['run_id', 'dataset_id', 'dataset_name', 'dataset_hash', 'created_at', 'method', 'k',
                         'precision', 'recall', 'mrr_at_k', 'ndcg', 'valid_precision_queries', 'valid_recall_queries',
                         'excluded_recall_queries', 'mean_ms', 'p50_ms', 'p95_ms', 'relevance_unit', 'positive_threshold',
                         'query_count', 'positive_label_count', 'model_id', 'model_version', 'dimension', 'search_version', 'policy_version',
                         'corpus_fingerprint', 'params_json', 'measurement_json', 'warnings'])
        from data_sync_etl.domain.evaluation import canonical
        for method, summary in run['summary'].items():
            for k, cells in summary['metrics'].items():
                row = [run['id'], run['dataset_id'], run['dataset_name'], run['dataset_hash'], run['created_at'], method, k,
                       *[cells[name]['value'] for name in ('precision', 'recall', 'mrr', 'ndcg')],
                       cells['precision']['valid_queries'], cells['recall']['valid_queries'], cells['recall']['excluded_queries'],
                       *[summary['latency_ms'][key] for key in ('mean', 'p50', 'p95')], 'resource', 1, len(run['queries']),
                       sum(label['grade'] >= 1 for query in run['queries'] for label in query['relevance']),
                       *[run['snapshot']['model'][key] for key in ('id', 'version', 'dimension')],
                       run['snapshot']['search_version'], run['snapshot']['policy_version'], run['corpus_fingerprint'],
                       canonical(run['params']), canonical(run['measurement']), '; '.join(run['warnings'])]
                writer.writerow([csv_cell(value) for value in row])
        return '\ufeff' + stream.getvalue()

"""Immutable T1 control + sanitized physical command accounting for T2 tests."""
import subprocess
from types import ModuleType

T1_SHA = '791c549a5649c4149f6fc9d996b3d830c6570988'


def t1_control(probe, monkeypatch):
    source = subprocess.check_output(['git', 'show', T1_SHA + ':src/data_sync_etl/application/search.py'])
    module = ModuleType('tests._immutable_t1_search')
    exec(compile(source, '<immutable-T1-' + T1_SHA[:7] + '>', 'exec'), module.__dict__)
    cls = module.SearchService
    for name, phase, options in (
        ('search_http', 'search_http', {'returned': True}),
        ('_ready_snapshot', 'ready_snapshot', {}),
        ('_allowed_units', 'allowed_units', {}),
        ('_snippets', 'snippets', {}),
    ):
        monkeypatch.setattr(cls, name, probe.wrap(getattr(cls, name), phase, **options))
    for name, phase in (('binary_score', 'scoring'), ('cosine', 'scoring'), ('rrf', 'ranking'),
                        ('encode_context', 'ranking')):
        monkeypatch.setattr(module, name, probe.wrap(getattr(module, name), phase))
    monkeypatch.setattr(module, 'sorted', probe.wrap(sorted, 'ranking'), raising=False)
    return cls


def command_summary(row):
    mongo = [op for op in row['operations'] if op['backend'] == 'mongo']
    text = [op for op in row['operations'] if op['backend'] == 'pg'
            and op.get('tables') == ['content_unit_texts']]
    def finds(collection, predicate):
        return sum(op['operation'] == 'find' and op['collection'] == collection
                   and predicate(op['phase']) for op in mongo)
    return {
        'SQL': row['PG_exec_count'], 'Mongo': row['Mongo_cmd_count'],
        'PG_text_reads': len(text), 'PG_text_ms': sum(op['ms'] for op in text),
        'current_fingerprints': finds('mongo_content_units', lambda p: 'get_bag' in p or 'search_bags' in p),
        'verification_bags': finds('content_bags', lambda p: 'verified' in p),
        'scoring_bags': finds('content_bags', lambda p: 'verified' not in p and ('get_bag' in p or 'search_bags' in p)),
        'PG_driver_ms': row['PG_exec_ms'], 'Mongo_driver_ms': row['Mongo_cmd_ms'],
        'model_prepare_ms': row['model_prepare_ms'], 'query_encode_ms': row['embed_ms'],
        'score_rank_ms': row['score_rank_ms'],
    }

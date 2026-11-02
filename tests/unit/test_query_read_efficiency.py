from sqlalchemy import event

from data_sync_etl.application import metadata_search
from data_sync_etl.domain.search_access import SearchAccess


def test_batch_source_authority_has_constant_database_roundtrips(container):
    container.sync.run(full=True)
    ids = [f'query-efficiency-{index}' for index in range(12)]
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'source_origin': 'LOCAL'})
        for number, key in enumerate(ids, 1):
            repo.insert('resource_versions', {'id': key, 'resource_id': 'resource-1',
                'version_number': number, 'lifecycle_status': 'INGESTED',
                'storage_bucket': 'test', 'storage_object_key': f'{number}.pdf', 'source_hash': 'a' * 64})
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    event.listen(container.engine, 'before_cursor_execute', capture)
    try:
        allowed = container.source_resolver.derived_index_authorized_many(
            ids, access=SearchAccess('authenticated_admin'))
    finally:
        event.remove(container.engine, 'before_cursor_execute', capture)
    assert allowed == dict.fromkeys(ids, True)
    assert len(statements) == 3
    statements.clear()
    event.listen(container.engine, 'before_cursor_execute', capture)
    try:
        with container.uow() as repo:
            initial = repo.search_authority_phase(ids)
    finally:
        event.remove(container.engine, 'before_cursor_execute', capture)
    assert len(statements) == 5
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'is_active': False})
    with container.uow() as repo:
        final = repo.search_authority_phase(ids)
        assert initial.bind(repo).get('master_learning_resources', 'resource-1')['is_active'] is True
        assert final.bind(repo).get('master_learning_resources', 'resource-1')['is_active'] is False


def test_fusion_sorts_each_branch_once_instead_of_once_per_result(monkeypatch):
    ranks = metadata_search.ranks
    calls = []
    def counted(branch):
        calls.append(len(branch))
        return ranks(branch)
    monkeypatch.setattr(metadata_search, 'ranks', counted)
    keys = [f'v{index:03}' for index in range(200)]
    content = [{'resource_version_id': key, 'resource_id': 'r' + key, 'score': 1.} for key in keys]
    branches = {'lexical': {key: 200 - index for index, key in enumerate(keys)},
                'dense': {key: index + 1 for index, key in enumerate(keys)}}
    results = metadata_search.fuse_results(content, [], branches)
    assert len(results) == 200 and calls == [200, 200]
    for row in results:
        assert row['score'] == sum(row['score_components'].values())

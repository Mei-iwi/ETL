from data_sync_etl.adapters.source.postgresql_ocr_queue import _running_task_query


def test_namespace_scope_counts_running_tasks_without_empty_in_bind():
    query, params = _running_task_query(frozenset(), 'source-admin')

    assert "FROM public.ocr_page_tasks WHERE status='PROCESSING'" in str(query)
    assert ' IN ' not in str(query)
    assert params == {}


def test_version_scoped_contract_keeps_version_and_actor_filter():
    query, params = _running_task_query(frozenset({'version-b', 'version-a'}), 'source-admin')

    assert 'j.resource_version_id IN' in str(query)
    assert params == {'versions': ['version-a', 'version-b'], 'actor': 'source-admin'}

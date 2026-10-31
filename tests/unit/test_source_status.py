from data_sync_etl.domain.source_status import normalize_source_status, source_status_allowed


def test_source_status_normalization_is_explicit_and_fail_closed():
    assert normalize_source_status(' published ') == 'PUBLISHED'
    assert normalize_source_status('archived') == 'DELETED'
    assert normalize_source_status('unknown-state') is None
    assert source_status_allowed('APPROVED', ('APPROVED',))
    assert not source_status_allowed('PUBLISHED', ('APPROVED',))
    assert not source_status_allowed('PRIVATE', ('PRIVATE', 'APPROVED'))
    assert not source_status_allowed('REJECTED', ('REJECTED', 'APPROVED'))

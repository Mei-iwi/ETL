from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from data_sync_etl.db.models import SyncReconciliationRun
from data_sync_etl.domain.core import DomainError, new_id, now


@pytest.fixture
def delta(container, tmp_path):
    source = create_engine('sqlite:///' + str(tmp_path / 'source.db'))
    old = now() - timedelta(days=1)
    with source.begin() as conn:
        conn.execute(text("CREATE TABLE subjects (id text primary key, updated_at timestamp, subject_name text, subject_slug text, grade_id text, is_active boolean, deleted_at timestamp)"))
        conn.execute(text("INSERT INTO subjects VALUES (:id,:stamp,'same','same',NULL,1,NULL)"),
                     [{'id': f's{i:04}', 'stamp': old.isoformat()} for i in range(1000)])
    with container.uow() as repo:
        for i in range(1000):
            repo.insert('subjects', {'id': f's{i:04}', 'subject_name': 'same', 'subject_slug': 'same',
                        'is_active': True, 'source_origin': 'POSTGRESQL', 'updated_at': old})
        repo.session.add(SyncReconciliationRun(run_id=new_id(), stream_name='subjects',
            status='COMPLETED', started_at=now() - timedelta(minutes=1)))
    engine = container.reconciliation_engine
    engine.source_adapter = SimpleNamespace(engine=source)
    yield container, engine, source
    source.dispose()


@pytest.mark.parametrize('changes', [0, 1, 2])
def test_delta_compares_only_changed_ids_and_replays_equal_timestamps(delta, changes):
    c, engine, source = delta
    with source.begin() as conn:
        for i in range(changes):
            conn.execute(text("UPDATE subjects SET updated_at=:stamp,subject_name='changed' WHERE id=:id"),
                         {'id': f's{i:04}', 'stamp': now().isoformat()})
    report = engine.execute_stream_reconcile('subjects')['reports'][0]
    assert report['source_count'] == report['target_count'] == changes
    assert report['mismatched_count'] == changes
    assert all(row['discrepancy_flag'] == 'DESYNC_METADATA' for row in report['discrepancies_sample'])
    assert report['match_rate_percent'] == 0
    assert report['summary_meta']['whole_repository_verified'] is False


def test_delta_missing_master_is_source_orphan_and_local_is_excluded(delta):
    c, engine, source = delta
    with source.begin() as conn:
        conn.execute(text("UPDATE subjects SET updated_at=:stamp WHERE id IN ('s0000','s0001')"), {'stamp': now().isoformat()})
    with c.uow() as repo:
        repo.delete('subjects', {'id': 's0000'})
        repo.update('subjects', 's0001', {'source_origin': 'LOCAL'})
    report = engine.execute_stream_reconcile('subjects')['reports'][0]
    assert [r['discrepancy_flag'] for r in report['discrepancies_sample']] == ['ORPHAN_SOURCE']


def test_engine_never_repairs_or_treats_unavailable_source_as_empty(delta):
    c, engine, source = delta
    with pytest.raises(DomainError, match='RECONCILIATION_READ_ONLY'):
        engine.execute_stream_reconcile('subjects', dry_run=False)
    engine.source_adapter = None
    with pytest.raises(DomainError, match='SOURCE_UNAVAILABLE'):
        engine.execute_stream_reconcile('subjects')
    with c.uow() as repo:
        assert repo.count('subjects', {}) == 1000


def test_canonical_target_hash_is_not_changed_by_audit_sqlite_timestamps(delta):
    from data_sync_etl.domain.source_ownership import state_hash
    c, engine, source = delta
    stamp = now()
    with source.begin() as conn:
        conn.execute(text("UPDATE subjects SET updated_at=:stamp WHERE id='s0000'"), {'stamp': stamp.isoformat()})
    with c.uow() as repo:
        repo.update('subjects', 's0000', {'updated_at': stamp})
        repo.update('subjects', 's0000', {'source_state_hash': state_hash(repo.get('subjects', 's0000'))})
    report = engine.execute_stream_reconcile('subjects')['reports'][0]
    assert report['mismatched_count'] == 0
    assert report['matched_count'] == 1

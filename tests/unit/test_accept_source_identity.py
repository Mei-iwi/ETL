from copy import deepcopy
from datetime import timedelta

import pytest

from data_sync_etl.domain.core import DomainError
from scripts.accept_source_identity import apply, preview, rollback


@pytest.fixture
def recreated(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    original = container.source.batches

    def recreated_batches(*args, **kwargs):
        for batch in original(*args, **kwargs):
            batch = deepcopy(batch)
            for row in batch:
                field = 'grade_created_at' if args[0] == 'grades' else 'created_at'
                row.values[field] += timedelta(days=1)
            yield batch

    container.source.batches = recreated_batches
    return container


def snapshot(container):
    from data_sync_etl.db.models import MODELS

    with container.uow() as repo:
        return {table: repo.find(table, order=tuple(c.name for c in MODELS[table].__table__.primary_key.columns)) for table in (
            'master_grade_levels', 'subjects', 'master_users', 'master_learning_resources',
            'resource_versions', 'resource_subjects', 'resource_grade_levels', 'sync_checkpoints')}


def test_preview_does_not_write_and_apply_unlocks_normal_sync(recreated, tmp_path):
    c = recreated
    before = snapshot(c)
    plan = preview(c.source, c.uow)
    assert plan['status'] == 'READY'
    assert len(plan['changes']) == sum(len(before[t]) for t in (
        'master_grade_levels', 'subjects', 'master_users', 'master_learning_resources'))
    assert snapshot(c) == before
    with pytest.raises(DomainError, match='BLOCKED_SOURCE_IDENTITY'):
        c.sync.run('grades', full=True)
    backup = tmp_path / 'backup.json'
    assert apply(c.source, c.uow, plan['fingerprint'], backup, 'target')['status'] == 'APPLIED'
    after = snapshot(c)
    assert after['resource_versions'] == before['resource_versions']
    assert after['resource_subjects'] == before['resource_subjects']
    assert after['sync_checkpoints'] == before['sync_checkpoints']
    assert preview(c.source, c.uow)['status'] == 'NO_CHANGES'
    c.sync.run(full=True)
    assert backup.exists()
    assert 'DEMO_NON_AUTHENTICATING_HASH' not in backup.read_text()


def test_changed_plan_refuses_all_writes(recreated, tmp_path):
    c = recreated
    before = snapshot(c)
    backup = tmp_path / 'backup.json'
    with pytest.raises(ValueError, match='Plan changed'):
        apply(c.source, c.uow, 'wrong', backup, 'target')
    assert snapshot(c) == before
    assert not backup.exists()


def test_rollback_restores_exact_rows_and_refuses_later_edits(recreated, tmp_path):
    c = recreated
    before = snapshot(c)
    plan = preview(c.source, c.uow)
    backup = tmp_path / 'backup.json'
    apply(c.source, c.uow, plan['fingerprint'], backup, 'target')
    assert rollback(c.uow, backup, 'target')['status'] == 'ROLLED_BACK'
    assert snapshot(c) == before
    backup2 = tmp_path / 'backup2.json'
    apply(c.source, c.uow, preview(c.source, c.uow)['fingerprint'], backup2, 'target')
    with c.uow() as repo:
        repo.update('subjects', 'subject-1', {'subject_name': 'Local edit'})
    after_edit = snapshot(c)
    with pytest.raises(ValueError, match='MASTER changed'):
        rollback(c.uow, backup2, 'target')
    assert snapshot(c) == after_edit


def test_local_changes_and_mapping_conflicts_are_not_bypassed(recreated):
    c = recreated
    with c.uow() as repo:
        repo.update('subjects', 'subject-1', {'subject_name': 'Local edit'})
    plan = preview(c.source, c.uow)
    assert plan['status'] == 'BLOCKED'
    assert any('BLOCKED_LOCAL_SOURCE_CHANGE' in row['reasons'] for row in plan['blocked'])


def test_existing_backup_never_overwritten(recreated, tmp_path):
    c = recreated
    before = snapshot(c)
    plan = preview(c.source, c.uow)
    backup = tmp_path / 'backup.json'
    backup.write_text('existing backup')
    with pytest.raises(FileExistsError):
        apply(c.source, c.uow, plan['fingerprint'], backup, 'target')
    assert snapshot(c) == before
    assert backup.read_text() == 'existing backup'

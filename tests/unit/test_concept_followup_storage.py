"""Checkpoint 03: local storage safety. Does not certify NFS/SMB/real ACLs."""
import shutil
import subprocess
import sys
from collections import namedtuple

import pytest

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.domain.concepts import empty_dictionary
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from tests.unit.test_dynamic_concept_dictionary import approve
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline


def build(store, *, parent=None, token='synthetic', records=(), activate=False):
    return store.build(records, seed_hash=empty_dictionary().fingerprint, source_token=token,
        ledger_hash='synthetic', expected_parent=parent, fence=lambda: True, activate=activate)


def test_artifact_preflight_reports_unverified_real_acl_without_mutation(tmp_path):
    store = ArtifactStore(tmp_path / 'offline', create=True)
    before = store.root.stat().st_mode
    result = store.preflight()
    assert result['live_status'] == 'BLOCKED_LIVE' and result['capacity_ok']
    assert result['acl_owner_verified'] is False and result['distributed_fencing_verified'] is False
    assert store.root.stat().st_mode == before


def test_atomic_publish_cas_rejects_stale_parent(pipeline):
    _, store, worker, candidate = pipeline
    approve(worker, candidate)
    first = worker.build()
    second = build(store, token='different')
    store.activate(first['generation'], fence=lambda _: True)
    with pytest.raises(ConceptBlocked, match='PARENT_CHANGED'):
        store.activate(second['generation'], fence=lambda _: True)
    assert store.current() == first['generation']


def test_worker_leader_lock_and_reader_lease_are_local_nonblocking(pipeline):
    _, store, worker, _ = pipeline
    release = worker.build()
    with store.worker_lock():
        with pytest.raises(ConceptBlocked, match='BUSY'), store.worker_lock():
            pytest.fail('Two local worker leaders')
    with store.reader_lease(release['generation']):
        with store.writer_lock():
            assert store._pinned_locked(release['generation'])
    with store.writer_lock():
        assert not store._pinned_locked(release['generation'])


def test_real_child_process_cannot_publish_while_local_writer_holds_lock(pipeline):
    store = pipeline[1]
    program = '''
import sys
from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.ports.concept_corpus import ConceptBlocked
store = ArtifactStore(sys.argv[1])
try:
    with store.writer_lock():
        print('ACQUIRED')
except ConceptBlocked:
    print('BLOCKED')
'''
    with store.writer_lock():
        blocked = subprocess.run([sys.executable, '-c', program, str(store.root)],
            capture_output=True, text=True, timeout=10, check=True)
    assert blocked.stdout.strip() == 'BLOCKED'
    allowed = subprocess.run([sys.executable, '-c', program, str(store.root)],
        capture_output=True, text=True, timeout=10, check=True)
    assert allowed.stdout.strip() == 'ACQUIRED'


def test_interrupted_publish_keeps_old_pointer_and_reports_recovery(pipeline, monkeypatch):
    _, store, worker, _ = pipeline
    first = worker.build()
    store.activate(first['generation'], fence=lambda _: True)
    second = build(store, parent=first['generation'], token='next')
    original = store.write_json

    def fail(relative, value):
        if relative == 'current.json':
            raise OSError('synthetic interruption')
        original(relative, value)

    monkeypatch.setattr(store, 'write_json', fail)
    with pytest.raises(OSError):
        store.activate(second['generation'], fence=lambda _: True)
    assert store.recovery_status()['status'] == 'INTERRUPTED'
    assert store.current() == first['generation']


def test_disk_full_refuses_write_and_no_temporary_pointer(pipeline, monkeypatch):
    _, store, _, _ = pipeline
    Usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: Usage(1, 1, 0))
    with pytest.raises(ConceptBlocked, match='DISK_BUDGET'):
        store.write_json('current.json', {'generation': '0' * 64})
    assert store.current() is None


@pytest.mark.parametrize('path', ['releases/file:stream', 'releases/name.', 'releases/name ', '../escape'])
def test_artifact_path_windows_aliases_rejected(pipeline, path):
    with pytest.raises(ConceptBlocked, match='PATH_FORBIDDEN'):
        pipeline[1].path(path)

from datetime import timedelta
import os

import pytest

from data_sync_etl.adapters.persistence.audit_queue import utc
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationFileState as FileState
from data_sync_etl.domain.core import now
from tests.unit.test_reconciliation_light_deep import local_file as local_file
from tests.unit.test_source_reference import source_viewer as source_viewer
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('change', ['same', 'stat', 'unknown', 'ttl', 'missing', 'checksum'])
def test_deep_proof_survives_only_unchanged_known_revision(local_file, monkeypatch, change):
    c, path, version = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.queue.enqueue()
    audit.once()
    assert audit.coverage()['recent_sha256_evidence_count'] == 1
    if change == 'stat':
        stamp = path.stat()
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 10000000))
    elif change == 'missing':
        path.unlink()
    elif change == 'checksum':
        path.write_bytes(b'corrupt!')
    elif change == 'ttl':
        with c.uow() as repo:
            repo.session.get(FileState, version).last_verified_at = now() - timedelta(days=1000)
    elif change == 'unknown':
        original = type(c.reconciliation_engine).verify_file_snapshot
        def unknown(self, *args, **kwargs):
            rows = original(self, *args, **kwargs)
            rows[0]['target_value'].pop('observed_revision', None)
            return rows
        monkeypatch.setattr(type(c.reconciliation_engine), 'verify_file_snapshot', unknown)
    audit.queue.enqueue('LIGHT', 'files')
    audit.once()
    assert audit.coverage()['recent_sha256_evidence_count'] == int(change == 'same')
    from types import SimpleNamespace
    from data_sync_etl.api.reconciliation_routes import file_evidence
    evidence = file_evidence(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=c))), version)
    assert evidence['recent_sha256_evidence'] == (change == 'same')
    assert 'storage_object_key' not in str(evidence) and str(path) not in str(evidence)
    with c.uow() as repo:
        state = repo.session.get(FileState, version)
        if change not in ('same', 'ttl'):
            assert utc(state.next_due_at) <= now()
    if change == 'checksum':
        audit.queue.enqueue()
        audit.once()
        with c.uow() as repo:
            assert repo.session.get(FileState, version).status == 'DESYNC_CHECKSUM'


@pytest.mark.parametrize('changed', [False, True])
def test_source_provider_revision_binds_deep_evidence(source_viewer, monkeypatch, changed):
    c, version, _, _ = source_viewer
    files = c.source_resolver.files
    original = files.revision
    revision = [None]
    def provider_revision(*args, **kwargs):
        return revision[0] if revision[0] is not None else original(*args, **kwargs)
    monkeypatch.setattr(files, 'revision', provider_revision)
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.queue.enqueue()
    audit.once()
    assert audit.coverage()['recent_sha256_evidence_count'] == 1
    if changed:
        ref = c.source_resolver.resolve(version['id'])
        current = original(ref.bucket, ref.opaque_locator)
        revision[0] = (*current[:4], 'changed-provider-revision')
    audit.queue.enqueue('LIGHT', 'files')
    audit.once()
    assert audit.coverage()['recent_sha256_evidence_count'] == int(not changed)

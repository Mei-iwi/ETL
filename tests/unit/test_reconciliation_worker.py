import os
from types import SimpleNamespace

import pytest

from data_sync_etl.config import Settings
from data_sync_etl.workers import reconciliation


@pytest.mark.parametrize('own_health_file', [False, True])
def test_audit_worker_preserves_inherited_sync_heartbeat(tmp_path, monkeypatch, own_health_file):
    sync_heartbeat = tmp_path / 'sync.json'
    sync_heartbeat.write_text('sync-owner')
    monkeypatch.setenv('ETL_WORKER_HEARTBEAT_FILE', str(sync_heartbeat))
    audit_heartbeat = tmp_path / 'audit.json'
    settings = Settings(_env_file=None, reconciliation_worker_heartbeat_file=audit_heartbeat if own_health_file else None)
    container = SimpleNamespace(settings=settings, reconciliation_engine=None, close=lambda: None)
    monkeypatch.setattr(reconciliation, 'Container', lambda _: container)
    class Audit:
        def __init__(self, *args): pass
        def schedule(self):
            assert os.environ.get('ETL_WORKER_HEARTBEAT_FILE') != str(sync_heartbeat)
        def once(self): return False
    monkeypatch.setattr(reconciliation, 'ScheduledAudit', Audit)
    monkeypatch.setattr('sys.argv', ['reconciliation', '--once'])
    reconciliation.main()
    assert sync_heartbeat.read_text() == 'sync-owner'
    assert os.environ['ETL_WORKER_HEARTBEAT_FILE'] == str(sync_heartbeat)
    assert not audit_heartbeat.exists()

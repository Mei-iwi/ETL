"""Offline maintenance entrypoint contract; never executes migrations."""
from pathlib import Path

import yaml


def test_owner_secret_mount_matches_maintenance_reader():
    root = Path(__file__).resolve().parents[2]
    service = yaml.safe_load((root / 'deploy/compose.ops.yml').read_text())['services']['ops']
    assert service['secrets'] == [{'source': 'ops-connections', 'target': 'ops-connections.json'}]
    assert service['environment']['INDEX_EMBEDDING_DIMENSION'] == '${INDEX_EMBEDDING_DIMENSION:-384}'
    assert service['command'] == ['approval-required'] and service['profiles'] == ['ops']
    assert service['read_only'] is True and service['restart'] == 'no'

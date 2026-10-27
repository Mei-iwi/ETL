"""P04 actual child process read lease against local GC; not multi-host proof."""
import subprocess
import sys
from time import monotonic, sleep

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from tests.unit.test_concept_followup_reuse_gc import generations


def test_child_process_reader_pin_is_respected_by_gc(tmp_path):
    store = ArtifactStore(tmp_path / 'local-artifacts', create=True)
    manifests = generations(store)
    old = manifests[0]['generation']
    ready = tmp_path / 'child-ready'
    code = '''
import sys
from pathlib import Path
from data_sync_etl.adapters.concept_artifacts import ArtifactStore
store = ArtifactStore(sys.argv[1])
with store.reader_lease(sys.argv[2]):
    Path(sys.argv[3]).write_text('ready', encoding='utf-8')
    sys.stdin.read(1)
'''
    child = subprocess.Popen([sys.executable, '-c', code, str(store.root), old, str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = monotonic() + 10
        while not ready.exists() and child.poll() is None and monotonic() < deadline:
            sleep(.02)
        assert ready.exists(), 'Child did not register its lease within budget'
        result = store.gc(retain=1, min_age_seconds=0, now=10**12)
        assert old in result['pinned'] and old not in result['candidates']
    finally:
        try:
            child.communicate(input='x', timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
    assert child.returncode == 0

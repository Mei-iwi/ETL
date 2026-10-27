"""Checkpoint 06: immutable reuse and two-phase GC on disposable derived roots."""
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from data_sync_etl.adapters.concept_artifacts import ArtifactStore, checksum
from data_sync_etl.domain.concepts import empty_dictionary
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from scripts.benchmark_concept_artifacts import records
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline


def test_million_scale_requires_separate_opt_in_before_creating_root(tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts/benchmark_concept_artifacts.py'
    target = tmp_path / 'not-created'
    result = subprocess.run([sys.executable, str(script), '--root', str(target), '--sizes', '1000000'],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and '--allow-million' in result.stderr
    assert not target.exists()


def make(store, values, parent=None, token='synthetic', **kwargs):
    return store.build(values, seed_hash=empty_dictionary().fingerprint, source_token=token,
        ledger_hash='synthetic-approval-only', expected_parent=parent, fence=lambda: True, **kwargs)


def test_shard_reuse_unchanged_and_seed_policy_change_rebuild(tmp_path):
    store = ArtifactStore(tmp_path / 'reuse', create=True)
    first = make(store, records(100), activate=True)
    second = make(store, records(100), first['generation'], token='new-authority')
    assert store.last_build_stats['reused'] == 4 and store.last_build_stats['rebuilt'] == 0
    assert second['source_token'] == 'new-authority'
    assert [s['sha256'] for s in first['shards']] == [s['sha256'] for s in second['shards']]
    store.build(records(100), seed_hash='different-seed', source_token='synthetic', ledger_hash='synthetic',
        expected_parent=first['generation'], fence=lambda: True)
    assert store.last_build_stats['rebuilt'] == 4


def test_shard_reuse_portable_copy_fallback_preserves_hashes(tmp_path, monkeypatch):
    import os
    store = ArtifactStore(tmp_path / 'copy-reuse', create=True)
    first = make(store, records(10), activate=True)

    def unavailable(*args):
        raise OSError('synthetic filesystem without hardlinks')

    monkeypatch.setattr(os, 'link', unavailable)
    second = make(store, records(10), first['generation'], token='fresh')
    assert store.last_build_stats['reused'] == 4 and store.last_build_stats['copied_bytes'] > 0
    assert store.last_build_stats['linked_bytes'] == 0
    assert [row['sha256'] for row in second['shards']] == [row['sha256'] for row in first['shards']]


def test_update_one_percent_and_revoke_rebuild_only_affected_shard(tmp_path):
    store = ArtifactStore(tmp_path / 'delta', create=True)
    values = list(records(100))
    first = make(store, values, activate=True)
    values[0] = {**values[0], 'definition': 'One percent changed, synthetic only.'}
    second = make(store, values, first['generation'])
    assert store.last_build_stats['reused'] == 3 and store.last_build_stats['rebuilt'] == 1
    first_file_hashes = [checksum(store.path(f"releases/{first['generation']}/{row['name']}")) for row in first['shards']]
    assert first_file_hashes == [row['sha256'] for row in first['shards']]
    third = make(store, values[1:], first['generation'])
    assert third['count'] == 99 and store.last_build_stats['rebuilt'] == 1
    assert store.load(third['generation']).lookup(['synthetic concept 000000']) == []
    bucket = int(sha256(values[0]['concept_id'].encode()).hexdigest()[:8], 16) % store.shards
    assert second['shards'][bucket]['sha256'] != first['shards'][bucket]['sha256']


def test_distributed_one_percent_can_require_all_shards(tmp_path):
    store = ArtifactStore(tmp_path / 'distributed-delta', create=True)
    values = list(records(400))
    first = make(store, values, activate=True)
    dirty = set()
    for index, value in enumerate(values):
        bucket = int(sha256(value['concept_id'].encode()).hexdigest()[:8], 16) % store.shards
        if bucket not in dirty:
            values[index] = {**value, 'definition': 'Distributed synthetic delta'}
            dirty.add(bucket)
        if len(dirty) == 4:
            break
    make(store, values, first['generation'])
    assert store.last_build_stats['reused'] == 0 and store.last_build_stats['rebuilt'] == 4


def generations(store):
    manifests = []
    for index in range(4):
        manifests.append(make(store, records(index), store.current(), token=str(index), activate=True))
    return manifests


def test_gc_dryrun_two_phase_and_pinned_generation(tmp_path):
    store = ArtifactStore(tmp_path / 'gc', create=True)
    releases = generations(store)
    first = releases[0]['generation']
    now = max(item['created_at'] for item in releases) + 100
    with store.reader_lease(first):
        result = store.gc(retain=1, min_age_seconds=0, now=now)
        assert first in result['pinned'] and first not in result['candidates']
    dryrun = store.gc(retain=1, min_age_seconds=0, now=now)
    assert first in dryrun['candidates'] and not dryrun['deleted']
    assert not store.path('gc').exists()
    marked = store.gc(retain=1, min_age_seconds=0, now=now, dry_run=False)
    assert first in marked['tombstoned'] and not marked['deleted']
    result = store.gc(retain=1, min_age_seconds=0, now=now + 61, dry_run=False)
    assert first in result['deleted']
    assert store.current() == releases[-1]['generation']
    assert store.load().manifest['count'] == 3
    assert store.load(releases[-2]['generation']).manifest['count'] == 2  # last-good protected


def test_gc_never_deletes_unknown_files_or_escapes_root(tmp_path):
    store = ArtifactStore(tmp_path / 'gc-safe', create=True)
    releases = generations(store)
    first = releases[0]['generation']
    now = max(item['created_at'] for item in releases) + 100
    store.gc(retain=1, min_age_seconds=0, now=now, dry_run=False)
    unexpected = store.path('releases/' + first + '/operator-evidence.txt')
    unexpected.write_text('preserve me', encoding='utf-8')
    with pytest.raises(ConceptBlocked, match='UNKNOWN_RELEASE_FILES'):
        store.gc(retain=1, min_age_seconds=0, now=now + 61, dry_run=False)
    assert unexpected.read_text(encoding='utf-8') == 'preserve me'


def test_gc_recovers_interrupted_trash_deletion(tmp_path, monkeypatch):
    from pathlib import Path
    store = ArtifactStore(tmp_path / 'gc-interrupted', create=True)
    releases = generations(store)
    first = releases[0]['generation']
    now = max(item['created_at'] for item in releases) + 100
    store.gc(retain=1, min_age_seconds=0, now=now, dry_run=False)
    original = Path.unlink

    def unavailable(path, *args, **kwargs):
        if 'trash' in path.parts and path.suffix == '.sqlite':
            raise PermissionError('synthetic locked file')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', unavailable)
        with pytest.raises(PermissionError):
            store.gc(retain=1, min_age_seconds=0, now=now + 61, dry_run=False)
    assert store.path('gc/trash/' + first).exists()
    result = store.gc(retain=1, min_age_seconds=0, now=now + 62, dry_run=False)
    assert first in result['resumed_retirements']
    assert not store.path('gc/trash/' + first).exists()
    assert store.load().manifest['count'] == 3


def test_gc_active_provider_generation_survives_switch(pipeline):
    from tests.unit.test_dynamic_concept_dictionary import activate, approve
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    old = store.current()
    with provider.pin('cache') as pinned:
        for index in range(3):
            make(store, records(index + 2), store.current(), token=inventory.token(), activate=True)
        assert provider.refresh()['generation'] != old
        result = store.gc(retain=1, min_age_seconds=0, now=10**12)
        assert old in result['pinned']
        assert pinned.expand('cache').terms
    provider.close()

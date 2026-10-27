"""Checkpoint 04: virtual clocks, synthetic sources, no live scheduler."""
from dataclasses import replace

import pytest

from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
from data_sync_etl.application.concept_scheduler import ConceptWorkerScheduler, SearchPressure
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline
from tests.unit.test_dynamic_concept_dictionary import resource


def test_scheduler_detects_edit_delete_restore_and_permission_without_new_version(pipeline):
    inventory, store, worker, _ = pipeline
    clock = [100.]
    worker.clock = lambda: clock[0]
    scheduler = ConceptWorkerScheduler(worker, lambda: inventory, clock=lambda: clock[0], reconcile_seconds=10)
    assert scheduler.step()['telemetry'] == 'FIXED_BUDGET_NO_TELEMETRY'
    original = inventory.current('r001')
    for index, changes in enumerate((dict(fields={'title': 'Changed text (cache)'}), dict(deleted=True),
                                    dict(deleted=False, public=False), dict(public=True)), start=2):
        clock[0] += 3
        original = replace(original, revision=index, **changes)
        inventory.put(original)
        state = scheduler.step()
        record = store.read_json(worker.contribution_key('r001'))
        assert record['fingerprint'] == scheduler.observed['r001']
        assert state['status'] == 'CANDIDATE_ONLY'
    assert worker.status()['urgent_revocations'] >= 2


def test_scheduler_pressure_source_outage_and_pause_do_not_use_old_source(pipeline):
    inventory, _, worker, _ = pipeline
    calls = []
    original = inventory.page

    def page(after, limit):
        calls.append(after)
        return original(after, limit)

    inventory.page = page
    scheduler = ConceptWorkerScheduler(worker, lambda: inventory,
        pressure=lambda: SearchPressure(active_requests=10))
    assert scheduler.step()['status'] == 'PAUSED_OR_WAITING'
    first = len(calls)
    scheduler.loader = lambda: (_ for _ in ()).throw(OSError('synthetic unavailable'))
    assert scheduler.step()['status'] == 'SOURCE_OR_PRESSURE_UNAVAILABLE'
    assert len(calls) == first


def test_tick_output_budget_replays_cursor_and_checkpoint(pipeline):
    inventory, store, _, _ = pipeline
    clock = [100.]
    worker = ConceptDiscoveryWorker(inventory, store, clock=lambda: clock[0], max_tick_bytes=1024)
    worker.reconcile()
    result = worker.tick()
    assert result['error_code'] == 'TICK_RESOURCE_BUDGET_REPLAY'
    assert result['after'] == '' and result['next_due'] > clock[0]


def test_partial_review_loss_is_urgent_even_when_resource_remains_public(pipeline):
    inventory, store, worker, _ = pipeline
    scheduler = ConceptWorkerScheduler(worker, lambda: inventory,
        pressure=lambda: SearchPressure(active_requests=10))
    scheduler.step()
    row = replace(inventory.current('r001'), revision=2, metadata_approved=False)
    inventory.put(row)
    assert scheduler.step()['status'] == 'PAUSED_OR_WAITING'
    assert worker.status()['urgent_revocations'] == 1
    assert store.read_json(worker.contribution_key(row.resource_id))['relations'] == []


def test_global_queue_stale_duplicate_and_bounded_backlog(pipeline):
    from data_sync_etl.application.concept_events import PendingConceptChanges
    from data_sync_etl.ports.concept_corpus import ConceptBlocked
    _, store, _, _ = pipeline
    queue = PendingConceptChanges(store, max_pending=2)
    queue.enqueue(resource(revision=3), 'update')
    assert not queue.enqueue(resource(revision=2), 'add')
    queue.enqueue(resource(2), 'add')
    with pytest.raises(ConceptBlocked, match='QUEUE_BUDGET'):
        queue.enqueue(resource(3), 'add')
    assert len(queue.peek()) == 2


def test_scheduler_source_backoff_survives_restart_with_virtual_clock(pipeline):
    _, store, worker, _ = pipeline
    clock = [100.]
    calls = []

    def unavailable():
        calls.append(1)
        raise OSError('synthetic unavailable')

    scheduler = ConceptWorkerScheduler(worker, unavailable, clock=lambda: clock[0])
    assert scheduler.step()['next_poll'] == 102
    restarted = ConceptWorkerScheduler(worker, unavailable, clock=lambda: clock[0])
    assert restarted.step()['status'] == 'SOURCE_BACKOFF' and len(calls) == 1
    clock[0] = 102
    assert restarted.step()['next_poll'] == 106 and len(calls) == 2
    assert store.read_json('checkpoints/scheduler.json')['failures'] == 2


def test_cli_worker_dryrun_pause_resume_preflight_and_gc_default(tmp_path, capsys):
    import json

    from data_sync_etl.cli.concepts import main
    root = tmp_path / 'operator'
    common = ['--root', str(root), '--fixture', str(root / 'example-corpus.json')]

    def run(action, *flags):
        assert main([action, *common, *flags]) == 0
        return json.loads(capsys.readouterr().out)

    run('example')
    assert run('run-worker')['status'] == 'DRY_RUN'
    assert not (root / 'checkpoints/state.json').exists()
    assert run('run-worker', '--apply')['status'] == 'CANDIDATE_ONLY'
    assert main(['pause', '--root', str(root)]) == 0
    assert json.loads(capsys.readouterr().out)['paused'] is True
    assert run('run-worker', '--apply')['status'] == 'PAUSED_OR_WAITING'
    assert main(['resume', '--root', str(root)]) == 0
    assert json.loads(capsys.readouterr().out)['paused'] is False
    assert run('gc')['dry_run'] is True
    assert run('preflight')['live_status'] == 'BLOCKED_LIVE'
    assert run('recover')['status'] == 'CONSISTENT'

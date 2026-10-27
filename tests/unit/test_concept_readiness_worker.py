"""P05 versioned checkpoints, conservative pressure, no live scheduler."""
from data_sync_etl.application.concept_scheduler import ConceptWorkerScheduler, SearchPressure
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline


def test_checkpoint_source_contract_rewinds_without_losing_pause_or_retry(pipeline):
    inventory, store, worker, _ = pipeline
    state = worker.status()
    state.update(after='old-resource', paused=True, failures=3, next_due=999)
    store.write_json('checkpoints/state.json', state)
    inventory.source_generation = 'another-trusted-fixture-reference'
    loaded = worker.status()
    assert loaded['after'] == '' and loaded['checkpoint_version'] == 2
    assert loaded['paused'] and loaded['failures'] == 3 and loaded['next_due'] == 999


def test_pressure_invalid_numbers_fail_conservatively():
    for value in (float('nan'), float('inf'), -1, True):
        assert SearchPressure(p95_ms=value).overloaded()


def test_unknown_telemetry_not_reported_healthy_and_bounded_jitter(pipeline):
    inventory, _, worker, _ = pipeline
    scheduler = ConceptWorkerScheduler(worker, lambda: inventory, clock=lambda: 100)
    scheduler.step()
    assert scheduler.health()['state'] != 'HEALTHY'
    scheduler.loader = lambda: (_ for _ in ()).throw(OSError('fixture unavailable'))
    scheduler.jitter = lambda: 10
    assert scheduler.step()['next_poll'] == 102.25
    assert scheduler.health()['state'] == 'SOURCE_UNAVAILABLE'


def test_five_logical_days_restart_outage_restore_without_real_sleep(pipeline):
    from dataclasses import replace
    inventory, store, worker, _ = pipeline
    now = [100.]
    worker.clock = lambda: now[0]
    scheduler = ConceptWorkerScheduler(worker, lambda: inventory, clock=lambda: now[0])
    for day in range(5):
        now[0] = 100 + day * 86400
        inventory.put(replace(inventory.current('r001'), revision=day + 2,
            fields={'title': f'Cache memory (cache) {day}'}, source_updated_at=now[0]))
        if day == 2:
            scheduler.loader = lambda: (_ for _ in ()).throw(OSError('synthetic outage'))
            assert scheduler.step()['status'] == 'SOURCE_OR_PRESSURE_UNAVAILABLE'
            scheduler = ConceptWorkerScheduler(worker, lambda: inventory, clock=lambda: now[0])
        else:
            assert scheduler.step()['status'] == 'CANDIDATE_ONLY'
    assert store.read_json(worker.contribution_key('r001'))['revision'] == 6

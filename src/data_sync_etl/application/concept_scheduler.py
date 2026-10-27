"""Bounded standalone concept scheduling; no ASGI startup or fabricated telemetry."""
from dataclasses import dataclass
from math import isfinite
from time import time

from data_sync_etl.domain.corpus_concepts import eligible, fingerprint
from data_sync_etl.ports.concept_corpus import ConceptBlocked


@dataclass(frozen=True)
class SearchPressure:
    active_requests: int | None = None
    p95_ms: float | None = None
    rss_bytes: int | None = None

    def overloaded(self):
        if any(value is not None and (type(value) not in (int, float) or not isfinite(value) or value < 0)
                for value in (self.active_requests, self.p95_ms, self.rss_bytes)):
            return True
        return bool((self.active_requests is not None and self.active_requests >= 4)
            or (self.p95_ms is not None and self.p95_ms >= 2000)
            or (self.rss_bytes is not None and self.rss_bytes >= 256 * 1024 * 1024))


class ConceptWorkerScheduler:
    """Loader supplies a bounded COMPLETE fixture inventory, not a live feed.

    Re-instantiation/restart rewinds the ordinary inventory cursor because no
    external watermark can prove that downtime changes were completely emitted.
    """
    def __init__(self, worker, loader, *, clock=time, pressure=None, reconcile_seconds=300, jitter=lambda: 0.):
        if not 1 <= reconcile_seconds <= 3600:
            raise ValueError('Bounded reconcile interval required')
        self.worker, self.loader, self.clock = worker, loader, clock
        self.pressure = pressure
        self.jitter = jitter
        self.reconcile_seconds, self.next_reconcile = reconcile_seconds, 0.
        self.observed = {}
        self.authority_state = {}
        self.poll_state = worker.store.read_json('checkpoints/scheduler.json', default={'failures': 0, 'next_poll': 0.})

    def _save_poll(self):
        with self.worker.store.writer_lock():
            self.worker.store.write_json('checkpoints/scheduler.json', self.poll_state)

    def health(self):
        state, pending = self.worker.status(), self.worker.events._read()
        urgent = sum(isinstance(item, dict) and item.get('kind') == 'revoke' for item in pending.values())
        status = 'DEGRADED'
        if self.poll_state.get('status') == 'SOURCE_OR_PRESSURE_UNAVAILABLE':
            status = 'SOURCE_UNAVAILABLE'
        elif state['paused']:
            status = 'PAUSED'
        elif not self.worker.fence.unchanged(self.worker.inventory.token()):
            status = 'BLOCKED_AUTHORITY'
        elif state['failures']:
            status = 'DEGRADED'
        elif pending:
            status = 'LAGGING'
        elif not self.observed:
            status = 'INITIALIZING'
        elif self.pressure:
            metric = self.pressure()
            if all(value is not None for value in (metric.active_requests, metric.rss_bytes, metric.p95_ms)) and not metric.overloaded():
                status = 'HEALTHY'
        return {'state': status, 'pending_urgent': urgent, 'pending_normal': len(pending) - urgent,
            'observed_at': self.clock(), 'live_status': 'BLOCKED_LIVE'}

    def step(self):
        if self.clock() < self.poll_state['next_poll']:
            return {'status': 'SOURCE_BACKOFF', 'next_poll': self.poll_state['next_poll']}
        try:
            inventory = self.loader()
            if inventory.scope != 'offline_fixture' or not inventory.completeness_verified:
                raise ConceptBlocked('LIVE_CHANGE_FEED_NOT_VERIFIED')
            inventory.set_available(True)
            # Fixture input is capped by CLI at 1000 rows. Cursor enumeration is
            # never used by Search; no source/media/database client is opened.
            after, seen, changes, next_authority = '', {}, [], {}
            while True:
                rows = inventory.page(after, 100)
                if not rows:
                    break
                for row in rows:
                    stamp = fingerprint(row)
                    old = self.observed.get(row.resource_id)
                    authority = (row.metadata_approved, row.approved_unit_ids, row.lifecycle, row.version_id)
                    if old != stamp:
                        previous = self.authority_state.get(row.resource_id)
                        lost = bool(previous and ((previous[0] and not row.metadata_approved)
                            or previous[1] - row.approved_unit_ids))
                        kind = ('revoke' if not eligible(row) or lost else 'review' if previous and previous != authority
                            else 'update' if old else 'add')
                        changes.append((row, kind))
                    seen[row.resource_id] = stamp
                    next_authority[row.resource_id] = authority
                    if len(seen) > 1000:
                        raise ConceptBlocked('SCHEDULER_INVENTORY_BUDGET')
                after = rows[-1].resource_id
            # A removed row must also invalidate the authority token. Contributions
            # are pruned at build/current checks; no fake tombstone is invented.
            self.worker.events.enqueue_many(changes)
            self.observed = seen
            self.authority_state = next_authority
            self.worker.inventory = inventory
            self.worker.fence.inventory = inventory
            inventory.set_review_token(self.worker.ledger.snapshot()[0])
            inventory.bind_review_authority(self.worker.ledger.authority_token)
            state = self.worker.status()
            if self.clock() >= self.next_reconcile and not state['paused'] and not state['failures']:
                self.worker.reconcile()
                self.next_reconcile = self.clock() + self.reconcile_seconds
            metric = self.pressure() if self.pressure else SearchPressure()
            self.worker.pressure = metric.overloaded
            result = {**self.worker.tick(), 'telemetry': 'INJECTED' if self.pressure else 'FIXED_BUDGET_NO_TELEMETRY'}
            self.poll_state = {'failures': 0, 'next_poll': 0., 'status': result['status']}
            self.poll_state['health'] = self.health()
            self._save_poll()
            return result
        except (OSError, ValueError, TypeError, ConceptBlocked):
            # Never process the previous authority while the loader is unavailable.
            invalidate = getattr(self.worker.inventory, 'set_available', None)
            if invalidate:
                invalidate(False)
            failures = min(20, self.poll_state['failures'] + 1)
            self.poll_state = {'status': 'SOURCE_OR_PRESSURE_UNAVAILABLE', 'failures': failures,
                'next_poll': self.clock() + min(300, 2 ** failures) + min(.25, max(0., self.jitter())), 'live_status': 'BLOCKED_LIVE'}
            self._save_poll()
            return self.poll_state

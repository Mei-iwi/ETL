"""Worker execution overlap with a bounded fake queue, NOT a PostgreSQL race test."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from time import perf_counter, sleep
from types import SimpleNamespace

from data_sync_etl.workers.source_file_import import SourceFileImportWorker


class Queue:
    def __init__(self, count, capacity, barrier=None):
        self.pending = deque(range(count))
        self.capacity, self.barrier = capacity, barrier
        self.lock = Lock()
        self.active, self.peak = 0, 0
        self.completed, self.durations = [], []
        self.settings = SimpleNamespace(source_file_import_lease_seconds=60)

    def discover_once(self):
        pass

    def claim(self, worker):
        with self.lock:
            if not self.pending or self.active >= self.capacity:
                return None
            job = {'id': self.pending.popleft(), 'worker': worker}
            self.active += 1
            self.peak = max(self.peak, self.active)
            return job

    def execute(self, job):
        started = perf_counter()
        if self.barrier:
            self.barrier.wait(timeout=3)
        sleep(.02)  # Fixed fake provider latency, no DB/Drive/model.
        with self.lock:
            self.completed.append(job['id'])
            self.durations.append(perf_counter() - started)
            self.active -= 1

    def heartbeat(self, job):
        pass


def test_two_source_worker_executions_overlap_on_distinct_jobs():
    queue = Queue(2, 2, Barrier(2))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: SourceFileImportWorker(queue).once(worker), ['a', 'b']))
    assert results == [True, True]
    assert queue.peak == 2 and sorted(queue.completed) == [0, 1]


def test_source_worker_bounded_fixture_benchmark(capsys):
    import json
    reports = []
    for concurrency in (1, 2):
        queue = Queue(8, concurrency)
        def work(worker):
            instance = SourceFileImportWorker(queue)
            while instance.once(worker):
                pass
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            started = perf_counter()
            list(pool.map(work, [f'fixture-{i}' for i in range(concurrency)]))
            wall = perf_counter() - started
        durations = sorted(queue.durations)
        assert sorted(queue.completed) == list(range(8)) and queue.peak <= concurrency
        reports.append({'workers': concurrency, 'jobs': 8, 'wall_seconds': round(wall, 4),
            'jobs_per_minute': round(480 / wall, 2), 'p50_seconds': durations[3], 'p95_seconds': durations[-1],
            'peak_in_flight': queue.peak, 'duplicate': len(queue.completed) - len(set(queue.completed)),
            'measurement': 'FAKE_QUEUE_FAKE_IO_NOT_DB_OR_PRODUCTION', 'rss': 'NOT_MEASURED'})
    # No speedup threshold: OS scheduling can dominate small fixtures.
    with capsys.disabled():
        print('BOUNDED_SOURCE_FIXTURE=' + json.dumps(reports))

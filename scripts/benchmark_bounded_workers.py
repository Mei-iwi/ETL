"""Synthetic queue benchmark ONLY, in a fresh isolated PostgreSQL test schema.

TEST_DATABASE_URL must pass scripts.verify.validate_test_url. No application .env
is loaded. Requires dev dependencies; psutil supplies optional CPU/RSS sampling.
Run: python -m scripts.benchmark_bounded_workers --output BOUNDED_WORKERS_BENCHMARK.json
"""
import argparse
import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event, Thread

from scripts.process_metrics import windows_process_sample
from scripts.verify import validate_test_url
from tests.conftest import pg_container
from tests.integration.test_bounded_workers import start_processes
from tests.unit.test_bounded_workers import seed_imports


def sample_resources(psutil, done, rss, cpu):
    if psutil is None:
        previous, previous_at = {}, time.perf_counter()
        while not done.is_set():
            current_at = time.perf_counter()
            total, seconds = 0, 0
            for pid in [os.getpid(), *[child.pid for child in mp.active_children()]]:
                value = windows_process_sample(pid)
                if value is not None:
                    total += value[0]
                    seconds += max(0, value[1] - previous.get(pid, value[1]))
                    previous[pid] = value[1]
            rss.append(total)
            cpu.append(100 * seconds / max(.001, current_at - previous_at))
            previous_at = current_at
            done.wait(.1)
        return
    parent = psutil.Process()
    tracked = {}
    while not done.is_set():
        total, usage = 0, 0
        for child in [parent, *parent.children(recursive=True)]:
            process = tracked.setdefault(child.pid, child)
            try:
                total += process.memory_info().rss
                usage += process.cpu_percent()
            except psutil.Error:
                pass
        rss.append(total)
        cpu.append(usage)
        done.wait(.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sizes', nargs='+', type=int, default=[100, 1000, 10000])
    parser.add_argument('--environment-note', default='No competing-load guarantee; operator must record background work.')
    args = parser.parse_args()
    validate_test_url(os.environ.get('TEST_DATABASE_URL'), os.environ)
    if any(size < 1 or size > 10000 for size in args.sizes):
        parser.error('sizes must be 1..10000')
    try:
        import psutil
    except ImportError:
        psutil = None
    report = {
        'scope': 'SYNTHETIC_SOURCE_IMPORT_QUEUE_ONLY',
        'limitations': [
            'No SOURCE file I/O, OCR, metadata scan, MongoDB or search workload.',
            'Includes process startup; no end-to-end throughput claim.',
            'Four workers not run: admitted capacity is capped at two.',
            'Single sample per configuration; not a production sizing recommendation.',
        ],
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'working_tree_changes': True,
        'environment_note': args.environment_note,
        'python': platform.python_version(), 'platform': platform.platform(),
        'dependencies': {name: importlib.metadata.version(name) for name in ('SQLAlchemy', 'psycopg', 'pytest')},
        'source_digest_sha256': hashlib.sha256(b''.join(
            path.relative_to(Path('src')).as_posix().encode() + b'\0' + path.read_bytes()
            for path in sorted(Path('src').rglob('*.py')))).hexdigest(),
        'total_memory_bytes': psutil.virtual_memory().total if psutil else None,
        'metrics_source': 'psutil' if psutil else 'Windows process APIs' if os.name == 'nt' else 'unavailable',
        'cpu_count': os.cpu_count(), 'results': [],
    }
    for size in args.sizes:
        for workers in (1, 2):
            with tempfile.TemporaryDirectory(prefix='etl-parallel-bench-') as directory:
                fixture = pg_container.__wrapped__(Path(directory))
                c = next(fixture)
                try:
                    report['postgres_version'] = list(c.engine.dialect.server_version_info)
                    seed_imports(c.uow, resources=size)
                    done = Event()
                    rss, cpu = [], []
                    thread = Thread(target=sample_resources, args=(psutil, done, rss, cpu), daemon=True) if psutil or os.name == 'nt' else None
                    started = time.perf_counter()
                    if thread:
                        thread.start()
                    try:
                        _, results = start_processes(c, [str(workers)] * workers, timeout=300)
                    finally:
                        done.set()
                        if thread:
                            thread.join(2)
                    duration = time.perf_counter() - started
                    ids = [key for result in results for key in result['ids']]
                    latencies = sorted(value for result in results for value in result['latencies'])
                    with c.uow() as repo:
                        rows = repo.find('source_file_imports')
                    assert len(ids) == len(set(ids)) == size
                    assert all(row['status'] == 'METADATA_ONLY' and row['attempts'] == 1 for row in rows)
                    record = {
                        'jobs': size, 'workers': workers, 'duration_seconds': duration,
                        'throughput_jobs_per_second': size / duration,
                        'p50_claim_and_commit_seconds': statistics.median(latencies),
                        'p95_claim_and_commit_seconds': latencies[min(len(latencies) - 1, int(.95 * len(latencies)))],
                        'process_tree_rss_mean_bytes': statistics.mean(rss) if rss else None,
                        'process_tree_rss_peak_bytes': max(rss) if rss else None,
                        'cpu_sample_peak_percent': max(cpu) if cpu else None,
                        'cpu_sample_mean_percent': statistics.mean(cpu) if cpu else None,
                        'cpu_metric_scope': 'sum of process-tree sampled percent; RSS includes benchmark parent',
                        'sql_statements': sum(result['sql_statements'] for result in results),
                        'worker_connections_opened': sum(result['connections_opened'] for result in results),
                        'source_queries': 0, 'source_bytes_read': 0,
                        'retry_count': 0, 'duplicate_count': 0, 'error_count': 0,
                        'checkpoint_correctness': 'NOT_EXERCISED_QUEUE_ONLY',
                    }
                    report['results'].append(record)
                    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                    print(f'{size} jobs / {workers} workers: {duration:.3f}s', flush=True)
                except Exception as error:
                    report.setdefault('incomplete', []).append({
                        'jobs': size, 'workers': workers, 'error_type': type(error).__name__,
                        'note': 'No throughput or completion claim for this configuration.'})
                    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                    raise
                finally:
                    try:
                        next(fixture)
                    except StopIteration:
                        pass
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()

"""Stop claiming after SIGTERM; let the current lease finish within stop grace."""
import json
import os
import signal
from contextlib import contextmanager
from pathlib import Path
from threading import Event, current_thread, main_thread


def tick():
    configured = os.environ.get('ETL_WORKER_HEARTBEAT_FILE')
    if configured:
        path = Path(configured)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'pid': os.getpid(), 'role': os.environ.get('ETL_SERVICE_ROLE', 'worker')}), encoding='utf-8')
        temporary.replace(path)


@contextmanager
def worker_lifecycle():
    stop = Event()
    previous = {}
    if current_thread() is main_thread():
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, lambda *_: stop.set())
    try:
        yield stop
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        configured = os.environ.get('ETL_WORKER_HEARTBEAT_FILE')
        if configured:
            Path(configured).unlink(missing_ok=True)

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from data_sync_etl.domain.core import new_id

correlation = ContextVar("correlation_id", default=None)
flow_started = ContextVar("flow_started", default=None)
FIELDS = {
    "run_id",
    "stream",
    "source_id",
    "target_id",
    "action",
    "duration_ms",
    "resource_id",
    "resource_version_id",
    "job_id",
    "task_id",
    "stage",
    "coverage",
}


def event(**fields):
    payload = {k: v for k, v in fields.items() if k in FIELDS}
    payload["correlation_id"] = correlation.get()
    started = flow_started.get()
    payload.setdefault("duration_ms", int((monotonic() - started) * 1000) if started else 0)
    logging.getLogger("etl").info(json.dumps(payload, default=str))


@contextmanager
def flow():
    token = correlation.set(correlation.get() or new_id())
    timer = flow_started.set(flow_started.get() or monotonic())
    try:
        yield correlation.get()
    finally:
        correlation.reset(token)
        flow_started.reset(timer)


def configure(level):
    logging.basicConfig(level=level, format="%(message)s")
    # SQL statements/parameters can include password hashes and document text.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

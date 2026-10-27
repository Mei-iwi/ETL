"""Bound local model admission using the existing PostgreSQL advisory locks."""

from contextlib import contextmanager
from time import monotonic, sleep

from data_sync_etl.domain.core import DomainError


@contextmanager
def media_capacity(uow, slots, timeout):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        for index in range(slots):
            with uow.processing_lock(f"media-capacity-{index}") as acquired:
                if acquired:
                    yield
                    return
        sleep(0.25)
    raise DomainError("MEDIA_CAPACITY_TIMEOUT")

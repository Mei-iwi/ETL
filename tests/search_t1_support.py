"""T1 test-only safety/fixture helpers; never use Dev data or retain response payloads."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

from data_sync_etl.api.search_schemas import MESSAGES


@contextmanager
def awake_test_host(set_state=None):
    """Thread-scoped idle-sleep prevention; no power-plan/display changes.

    Does not override lid closure or explicit sleep. Restore this thread's
    previous execution state even when pytest/model/infrastructure fails.
    """
    if set_state is None:
        if os.name != 'nt':
            yield False
            return
        import ctypes

        set_state = ctypes.WinDLL('kernel32', use_last_error=True).SetThreadExecutionState
        set_state.argtypes = [ctypes.c_uint]
        set_state.restype = ctypes.c_uint
    previous = set_state(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED, never DISPLAY_REQUIRED.
    if not previous:
        raise RuntimeError('Cannot establish controlled awake test host')
    try:
        yield True
    finally:
        if not set_state(previous | 0x80000000):
            raise RuntimeError('Cannot restore test thread execution state')


def active_host_seconds():
    """Independent test clock excluding host sleep; NEVER changes Search deadlines."""
    if os.name != 'nt':
        return None
    import ctypes

    value = ctypes.c_ulonglong()
    clock = ctypes.WinDLL('kernel32', use_last_error=True).QueryUnbiasedInterruptTime
    clock.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    clock.restype = ctypes.c_int
    if not clock(ctypes.byref(value)):
        raise RuntimeError('Cannot sample independent active-host clock')
    return value.value / 10_000_000


class UnexpectedSearch5xx(AssertionError):
    pass


class FailureCheckpoint:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            raise FileExistsError('Refusing to overwrite T1 failure evidence')
        self.events = []

    def observe(self, response, *, method, request_id, duration_ms, fixture,
                expected=None, simulated=False):
        if response.status_code < 500:
            return
        try:
            body = response.json()
            detail = body.get('detail') if isinstance(body, dict) else None
            value = detail.get('code') if isinstance(detail, dict) else None
        except ValueError:
            value = None
        # Never persist arbitrary server/proxy text, including unknown error codes.
        code = value if isinstance(value, str) and value in MESSAGES else 'UNCLASSIFIED_5XX'
        phase = {'SEARCH_RUNTIME_BUSY': 'admission', 'SEARCH_TIMEOUT': 'deadline',
                 'BLOCKED_EMBEDDING_RUNTIME': 'embedding', 'INVALID_EMBEDDING': 'embedding',
                 'SEARCH_MODEL_WARMUP_TIMEOUT': 'model_prepare',
                 'BLOCKED_INDEX_SCHEMA': 'schema', 'SEARCH_SCHEMA_UNAVAILABLE': 'schema'}.get(code, 'unknown')
        approved = response.status_code == 503 and (code == 'SEARCH_RUNTIME_BUSY' or
                                                   simulated and code == expected)
        self.events.append({'request_id': int(request_id), 'status': int(response.status_code),
            'code': code, 'phase': phase, 'method': method if method in ('bow', 'vector', 'hybrid') else 'unknown',
            'cache': 'OFF', 'admission': 'BUSY' if code == 'SEARCH_RUNTIME_BUSY' else 'NOT_BUSY',
            'duration_ms': float(duration_ms), 'fixture': fixture if fixture in ('small', 'medium', 'structured', 'unit') else 'unknown',
            'simulated': bool(simulated), 'expected': bool(approved)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Checkpoint BEFORE any assertion aborts the run. All fields above are allowlisted.
        self.path.write_text(json.dumps({'events': self.events}, indent=2) + '\n', encoding='utf-8')
        if not approved:
            raise UnexpectedSearch5xx('Unexpected Search5xx persisted; T1 gate closed')

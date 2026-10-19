"""Bounded per-process HTTP Search work, not a worker, cache or user session."""

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Condition, Event, Lock
from time import monotonic

from data_sync_etl.domain.search import SearchError

CURRENT_SEARCH = ContextVar('http_search_call', default=None)


class SearchCall:
    """One request's budget/cancellation. No principal, query or result retained.

    A timed-out HTTP waiter may abandon a blocking driver/model call. Its worker
    capacity remains owned until that actual worker exits, not until HTTP exits.
    """

    def __init__(self, timeout, release, wake, *, clock=monotonic, authorize=None):
        self.clock, self.started = clock, clock()
        self.deadline = self.started + timeout
        self.cancelled = Event()
        self._release, self._wake = release, wake
        self._lock = Lock()
        self._claimed = self._closed = False
        self.queue_ms = self.inference_ms = 0.0
        self.cancel_reason = None
        self._authorize = authorize  # Request-local callback; never shared/cached.

    def remaining(self):
        return max(0.0, self.deadline - self.clock())

    def check(self):
        if self.cancelled.is_set() or self.remaining() <= 0:
            raise SearchError('SEARCH_TIMEOUT')

    def reauthorize(self):
        self.check()
        if self._authorize is not None:
            self._authorize()

    def claim(self):
        with self._lock:
            if self._closed or self._claimed:
                return False
            self._claimed = True
            return True

    def finish(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._release()

    def cancel(self, reason='cancel'):
        self.cancel_reason = reason
        self.cancelled.set()
        self._wake()

    def abandon(self):
        # Atomic with claim: cancellation BEFORE dispatch releases the lease;
        # cancellation AFTER dispatch never releases a still-running worker.
        with self._lock:
            if self._closed:
                return
            self.cancelled.set()
            if not self._claimed:
                self._closed = True
                self._release()
        self._wake()


class SearchInferenceQueue:
    """One inference + at most four FIFO waiters in THIS process only.

    No extra threads are created. Preparation and encoding are serialized; DB,
    scoring, BoW and other processes are not locked by this scheduler.
    """

    def __init__(self, max_waiting=4):
        self._condition = Condition()
        self._queue = deque()
        self._running = False
        self.max_waiting = max_waiting

    def wake(self):
        with self._condition:
            self._condition.notify_all()

    @contextmanager
    def slot(self, call):
        ticket, queued = object(), call.clock()
        with self._condition:
            call.check()
            if len(self._queue) >= self.max_waiting:
                raise SearchError('SEARCH_RUNTIME_BUSY')
            self._queue.append(ticket)
            self._condition.notify_all()
            try:
                while self._running or self._queue[0] is not ticket:
                    call.check()
                    self._condition.wait(min(.05, call.remaining()))
                call.check()
                self._queue.popleft()
                self._running = True
            except BaseException:
                call.queue_ms += (call.clock() - queued) * 1000
                self._queue.remove(ticket)
                self._condition.notify_all()
                raise
        acquired = call.clock()
        call.queue_ms += (acquired - queued) * 1000
        try:
            yield
        finally:
            call.inference_ms += (call.clock() - acquired) * 1000
            with self._condition:
                self._running = False
                self._condition.notify_all()

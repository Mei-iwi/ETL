"""HTTP-only pure query-vector reuse. Never stores retrieval or authorization."""

import hashlib
import hmac
import secrets
import sys
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from data_sync_etl.domain.indexing import validate_vector
from data_sync_etl.domain.search import normalize_query

QUERY_CONTRACT = 'search-nfc-strip-local-sentence-cpu-default-encode-v1'
SCOPE = 'verified-http-local-demo-v1'
COUNTERS = ('hit', 'miss', 'published', 'expired', 'evicted', 'invalidated',
            'bypass_runtime', 'bypass_limits', 'bypass_disabled', 'bypass_scope', 'failed')


@dataclass(frozen=True, slots=True)
class Entry:
    vector: tuple
    created: float
    cost: int


class HttpQueryVectorCache:
    """The composition root attests the fixed local encoder, never a client.

    Weak owner + function affinity rejects replacement/unknown encoders. Loaded
    model artifacts remain subject to the existing immutable revision contract.
    Byte accounting is conservative cache heap accounting, not a process RSS cap.
    """

    def __init__(self, runtime, *, clock=monotonic, ttl=60, max_entries=64, max_bytes=1024 * 1024):
        if not 0 < ttl <= 60 or not 1 <= max_entries <= 64 or not 8192 <= max_bytes <= 1024 * 1024:
            raise ValueError('Invalid query cache bounds')
        self._clock, self.ttl, self.max_entries, self.max_bytes = clock, ttl, max_entries, max_bytes
        self._lock = Lock()
        self._entries = OrderedDict()
        self._cost = 0
        self._secret = secrets.token_bytes(32)
        self._stats = dict.fromkeys(COUNTERS, 0)
        try:
            self._owner = weakref.ref(runtime)
        except TypeError:
            self._owner = lambda: None  # Unknown/unweakrefable runtimes bypass, even with flag OFF.
        self._signature = tuple(getattr(runtime, field, None) for field in ('model_id', 'version', 'dimension'))
        self._encoder = getattr(getattr(runtime, '_encode', None), '__func__', None)
        self._loaded_owner = None

    def count(self, name):
        if name not in self._stats:
            raise ValueError('Unknown bounded counter')
        with self._lock:
            self._stats[name] += 1

    def _bytes(self):
        # Includes fixed owner/secret/lock/stats/object budget and live dict size.
        return 4096 + sys.getsizeof(self._entries) + self._cost

    def _remove(self, key):
        self._cost -= self._entries.pop(key).cost

    def _prune(self, stamp):
        for key, entry in list(self._entries.items()):
            if stamp - entry.created >= self.ttl:
                self._remove(key)
                self._stats['expired'] += 1

    def clear(self):
        with self._lock:
            self._stats['invalidated'] += len(self._entries)
            self._entries.clear()
            self._cost = 0

    def stats(self):
        with self._lock:
            self._prune(self._clock())
            return {**self._stats, 'entries': len(self._entries), 'accounted_bytes': self._bytes(),
                    'max_entries': self.max_entries, 'max_bytes': self.max_bytes}

    def _trusted(self, runtime):
        try:
            valid = (runtime is self._owner() and self._signature[2] == 384 and self._encoder is not None
                     and (runtime.model_id, runtime.version, runtime.dimension) == self._signature
                     and bool(runtime.ready())
                     and getattr(getattr(runtime, '_encode', None), '__func__', None) is self._encoder)
            if not valid:
                return False
            # The attested local adapter exposes its loaded encoder only after
            # prepare. Replacing that instance is not the same cache generation.
            loaded = runtime._model
            if loaded is None:
                return False
            with self._lock:
                if self._loaded_owner is None:
                    self._loaded_owner = weakref.ref(loaded)
                return self._loaded_owner() is loaded
        except Exception:
            return False

    def _key(self, query, scope):
        # Bound lengths before encoding; raw query exists only in the active call.
        values = (QUERY_CONTRACT, *self._signature, scope, query)
        payload = repr(values).encode('utf-8')
        return hmac.digest(self._secret, payload, hashlib.sha256)

    def _get(self, key):
        with self._lock:
            self._prune(self._clock())
            entry = self._entries.get(key)
            if entry is None:
                self._stats['miss'] += 1
                return None
            self._entries.move_to_end(key)
            self._stats['hit'] += 1
            return entry.vector

    def _put(self, key, vector):
        stamp = self._clock()
        cost = (sys.getsizeof(key) + sys.getsizeof(vector)
                + sum(sys.getsizeof(value) for value in vector) + 512)
        with self._lock:
            self._prune(stamp)
            if cost + 4096 + sys.getsizeof(self._entries) > self.max_bytes:
                self._stats['bypass_limits'] += 1
                return
            if key in self._entries:
                return  # A hit, including a competing fill, never extends TTL.
            while self._entries and len(self._entries) >= self.max_entries:
                self._remove(next(iter(self._entries)))
                self._stats['evicted'] += 1
            self._entries[key] = Entry(vector, stamp, cost)
            self._cost += cost
            while self._entries and self._bytes() > self.max_bytes:
                self._remove(next(iter(self._entries)))
                self._stats['evicted'] += 1
            if key in self._entries:
                self._stats['published'] += 1

    def request(self, scope=SCOPE):
        return QueryVectorRequest(self, scope)


class QueryVectorRequest:
    """Stage at most one vector; publish only on successful HTTP Search return."""

    def __init__(self, cache, scope):
        self.cache, self.scope, self._pending = cache, scope, None

    def __enter__(self):
        return self

    def encode(self, runtime, encoder, query, dimension):
        cache = self.cache
        known = (self.scope == SCOPE and dimension == 384 and isinstance(query, str)
                 and 1 <= len(query) <= 300 and query == normalize_query(query)
                 and cache._trusted(runtime)
                 and getattr(encoder, '__self__', None) is runtime
                 and getattr(encoder, '__func__', None) is cache._encoder)
        if not known:
            cache.clear()
            cache.count('bypass_runtime')
            return validate_vector(encoder(query), dimension)
        key = cache._key(query, self.scope)
        vector = cache._get(key)
        if vector is not None:
            try:
                return validate_vector(vector, 384)
            except Exception:
                cache.clear()
                raise
        vector = validate_vector(encoder(query), 384)
        self._pending = (key, tuple(vector))
        return vector

    def commit(self):
        if self._pending is not None:
            runtime = self.cache._owner()
            if runtime is not None and self.cache._trusted(runtime):
                self.cache._put(*self._pending)
            else:
                self.cache.clear()
                self.cache.count('bypass_runtime')
        self._pending = None

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            self.cache.count('failed')
            self.cache.clear()
        self._pending = None

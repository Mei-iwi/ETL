"""Bounded rank reuse with request-time authority and hydration.

No production authority adapter is implied by this module. Composition must
provide a trusted revision fence covering ACL, metadata, content, model, source
and dictionary. Without it sessions cannot be used. Never cache result DTOs.
"""
import secrets
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass
from threading import RLock
from time import monotonic

from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.search import SearchError


@dataclass(frozen=True, slots=True)
class RankedHit:
    resource_id: str
    version_id: str
    bag_id: str | None
    unit_id: str | None
    score: float


@dataclass(frozen=True, slots=True)
class RankSnapshot:
    owner: str
    request_key: str
    revision: str
    hits: tuple[RankedHit, ...]
    created: float
    cost: int


class SearchResultSessions:
    def __init__(self, authority, *, clock=monotonic, ttl=30., max_entries=32,
                 max_bytes=1024 * 1024, per_owner=4, max_candidates=200, wait_seconds=2.):
        if (not callable(authority) or not 0 < ttl <= 60 or not 1 <= max_entries <= 128
                or not 4096 <= max_bytes <= 16 * 1024 * 1024 or not 1 <= per_owner <= 16
                or not 1 <= max_candidates <= 1000 or not 0 < wait_seconds <= 5):
            raise ValueError('Trusted authority and bounded session limits required')
        self.authority, self.clock, self.ttl = authority, clock, ttl
        self.max_entries, self.max_bytes, self.per_owner = max_entries, max_bytes, per_owner
        self.max_candidates, self.wait_seconds = max_candidates, wait_seconds
        self._entries, self._pending, self._lock = OrderedDict(), {}, RLock()
        self._bytes = 0
        self.metrics = dict(hit=0, miss=0, evicted=0, invalidated=0, published=0)

    def _revision(self, owner):
        if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
            raise SearchError('SEARCH_SESSION_UNAVAILABLE', 409)
        revision = self.authority(owner)
        if not isinstance(revision, str) or not revision:
            raise SearchError('SEARCH_SESSION_UNAVAILABLE', 409)
        return revision

    def _drop(self, token):
        self._bytes -= self._entries.pop(token).cost

    def invalidate_owner(self, owner):
        with self._lock:
            for token in list(self._entries):
                if self._entries[token].owner == owner:
                    self._drop(token)
                    self.metrics['invalidated'] += 1

    def begin(self, owner, request_key, rank):
        """rank returns (complete bounded ordered hits, has_more); no snippets."""
        revision = self._revision(owner)
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
            raise SearchError('SEARCH_SESSION_UNAVAILABLE', 409)
        key = digest([owner, request_key, revision])
        with self._lock:
            for token, entry in self._entries.items():
                if (entry.owner == owner and entry.request_key == request_key and entry.revision == revision
                        and self.clock() - entry.created < self.ttl):
                    return token
            future = self._pending.get(key)
            leader = future is None
            if leader:
                if len(self._pending) >= self.max_entries:
                    raise SearchError('SEARCH_SESSION_BUSY', 503)
                future = self._pending[key] = Future()
        if not leader:
            try:
                return future.result(timeout=self.wait_seconds)
            except TimeoutError as exc:
                raise SearchError('SEARCH_SESSION_BUSY', 503) from exc
        try:
            hits, has_more = rank()
            if has_more or len(hits) > self.max_candidates:
                raise SearchError('SEARCH_SESSION_TRUNCATED', 409)
            import math

            identities = set()
            for hit in hits:
                if (type(hit) is not RankedHit or not math.isfinite(hit.score)
                        or any(value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 80)
                               for value in (hit.resource_id, hit.version_id, hit.bag_id, hit.unit_id))
                        or not hit.resource_id or not hit.version_id or hit.resource_id in identities):
                    raise SearchError('SEARCH_SESSION_INVALID_RANK', 409)
                identities.add(hit.resource_id)
            if revision != self._revision(owner):
                raise SearchError('SEARCH_SESSION_STALE', 409)
            cost = 1024 + sum(512 + sum(len(value or '') * 4 for value in (
                hit.resource_id, hit.version_id, hit.bag_id, hit.unit_id)) for hit in hits)
            if cost > self.max_bytes:
                raise SearchError('SEARCH_SESSION_CAPACITY', 409)
            token = secrets.token_urlsafe(32)
            snapshot = RankSnapshot(owner, request_key, revision, tuple(hits), self.clock(), cost)
            with self._lock:
                for previous in list(self._entries):
                    if self.clock() - self._entries[previous].created >= self.ttl:
                        self._drop(previous)
                owned = [item for item in self._entries if self._entries[item].owner == owner]
                while len(owned) >= self.per_owner:
                    self._drop(owned.pop(0))
                    self.metrics['evicted'] += 1
                while self._entries and (len(self._entries) >= self.max_entries or self._bytes + cost > self.max_bytes):
                    self._drop(next(iter(self._entries)))
                    self.metrics['evicted'] += 1
                self._entries[token] = snapshot
                self._bytes += cost
                self.metrics['published'] += 1
            future.set_result(token)
            return token
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def page(self, token, owner, request_key, page, size, hydrate):
        if type(page) is not int or type(size) is not int or page < 1 or not 1 <= size <= 50:
            raise SearchError('SEARCH_SESSION_PAGE_INVALID', 422)
        revision = self._revision(owner)
        with self._lock:
            snapshot = self._entries.get(token)
            if (snapshot is None or snapshot.owner != owner or snapshot.request_key != request_key):
                self.metrics['miss'] += 1
                raise SearchError('SEARCH_SESSION_MISS', 409)
            if self.clock() - snapshot.created >= self.ttl or revision != snapshot.revision:
                self._drop(token)
                self.metrics['invalidated'] += 1
                raise SearchError('SEARCH_SESSION_MISS', 409)
            self._entries.move_to_end(token)
        selected = snapshot.hits[(page - 1) * size:page * size]
        # The trusted hydrator must authorize every requested ID and return
        # current evidence. Missing/denied items invalidate, never leak old total.
        items = hydrate(selected)
        if (len(items) != len(selected) or self._revision(owner) != revision
                or self.clock() - snapshot.created >= self.ttl
                or any(item.get('resource_id') != hit.resource_id or item.get('resource_version_id') != hit.version_id
                       for item, hit in zip(items, selected, strict=False))):
            with self._lock:
                if token in self._entries:
                    self._drop(token)
            raise SearchError('SEARCH_SESSION_MISS', 409)
        self.metrics['hit'] += 1
        return {'items': items, 'page': page, 'size': size, 'total': len(snapshot.hits),
                'total_pages': (len(snapshot.hits) + size - 1) // size}

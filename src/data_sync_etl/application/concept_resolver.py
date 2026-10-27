"""Generation-pinned query dictionaries. Background artifact IO; no corpus IO.

No live attestor is installed by composition. Explicit fixture authority is
required for offline tests; a configuration flag alone cannot grant that scope.
"""
from collections import OrderedDict, deque
from contextlib import contextmanager
from threading import Event, RLock, Thread
from time import monotonic, time

from data_sync_etl.domain.concepts import Concept, Dictionary, Snapshot, normalize
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import QUERY_STOPWORDS, surface_tokens
from data_sync_etl.ports.concept_corpus import ConceptBlocked


class ConceptDictionaryProvider:
    def __init__(self, base, *, store=None, enabled=False, fence=None, allow_offline=False,
                 cache_bytes=16 * 1024 * 1024, lookup_ms=20, max_records=32, refresh_seconds=10.):
        self.base, self.enabled, self.fence, self.allow_offline = base, enabled, fence, allow_offline
        self.store = store if fence is not None and allow_offline else None
        self.cache_bytes, self.lookup_ms, self.max_records = cache_bytes, lookup_ms, max_records
        self.refresh_seconds = refresh_seconds
        self._active, self._cache, self._bytes = None, OrderedDict(), 0
        self._lock, self._stop, self._thread = RLock(), Event(), None
        self._killed = False
        self._leases = {}
        self._latencies = deque(maxlen=256)
        self.metrics = {'status': 'DISABLED' if not enabled else 'BLOCKED_LIVE', 'hits': 0, 'misses': 0,
                        'fallbacks': 0, 'lookups': 0, 'lookup_ms_total': 0.}

    def _retain_locked(self, release):
        key = release.manifest['generation']
        if key not in self._leases:
            lease = self.store.reader_lease(key)
            lease.__enter__()
            self._leases[key] = [lease, 0]
        self._leases[key][1] += 1

    def _release_locked(self, release):
        if release is None:
            return
        key = release.manifest['generation']
        entry = self._leases.get(key)
        if entry:
            entry[1] -= 1
            if not entry[1]:
                del self._leases[key]
                entry[0].__exit__(None, None, None)

    def _valid(self, release):
        return bool(self.enabled and not self._killed and release and self.fence is not None
            and self.allow_offline and self.fence.inventory.scope == 'offline_fixture'
            and release.manifest['scope'] == 'offline_fixture'
            and release.manifest.get('revocation_completeness') == 'fixture_only'
            and release.manifest['seed_hash'] == self.base.fingerprint
            and self.fence.unchanged(release.manifest['source_token']))

    def refresh(self):
        """Explicit operator/background action; never called by pin or Search."""
        if not self.enabled or self.store is None or self.fence is None or not self.allow_offline:
            return self.status()
        try:
            release = self.store.load()
            if not self._valid(release):
                raise ConceptBlocked('BLOCKED_PRIVACY')
            with self._lock:
                previous = self._active
                if previous is None or previous.manifest['generation'] != release.manifest['generation']:
                    self._retain_locked(release)
                    self._release_locked(previous)
                self._active = release
                self.metrics['status'] = 'OFFLINE_READY'
        except Exception:
            with self._lock:
                # A different broken release can keep a separately verified old
                # generation. A corrupted currently active directory cannot.
                keep = False
                if self._valid(self._active):
                    try:
                        self.store.load(self._active.manifest['generation'])
                        keep = True
                    except Exception:
                        pass
                if not keep:
                    self._release_locked(self._active)
                    self._active = None
                self.metrics['status'] = 'LAST_GOOD' if keep else 'SEED_FALLBACK'
        return self.status()

    def status(self):
        with self._lock:
            latencies = sorted(self._latencies)
            return {**self.metrics, 'status': ('BLOCKED_PRIVACY' if self._active and not self._valid(self._active)
                                             else self.metrics['status']),
                'generation': self._active.manifest['generation'] if self._active else None,
                'cache_bytes_estimated': self._bytes, 'cache_entries': len(self._cache),
                'snapshot_age_seconds': max(0., time() - self._active.manifest['created_at']) if self._active else None,
                'lookup_p50_ms': latencies[len(latencies) // 2] if latencies else None,
                'lookup_p95_ms': latencies[int(.95 * (len(latencies) - 1))] if latencies else None,
                'live_accepted': False}

    def kill(self):
        with self._lock:
            self._killed = True
            self._release_locked(self._active)
            self._active = None
            self._cache.clear()
            self._bytes = 0
            self.metrics['status'] = 'BLOCKED_PRIVACY'

    def start(self):
        if self._thread is not None or not self.enabled or self.fence is None or not self.allow_offline:
            return
        self._stop.clear()
        def poll():
            while not self._stop.is_set():
                self.refresh()
                self._stop.wait(self.refresh_seconds)
        self._thread = Thread(target=poll, daemon=True, name='concept-artifact-loader')
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            self._release_locked(self._active)
            self._active = None

    def _resolve(self, release, query, domain):
        key = digest([release.manifest['generation'], query, domain])
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self.metrics['hits'] += 1
                return cached[0]
            self.metrics['misses'] += 1
        tokens = surface_tokens(query)
        if len(tokens) > 160:
            raise ConceptBlocked('LOOKUP_BUDGET')
        if len(tokens) <= 32:
            aliases = {normalize(' '.join(tokens[start:start + size])) for start in range(len(tokens))
                       for size in range(1, min(6, len(tokens) - start) + 1)}
        else:
            # Candidate lookup only; Dictionary.expand still sees the full raw
            # query, including negation. Never rewrite the Vector input.
            aliases = {normalize(token) for token in tokens if token not in QUERY_STOPWORDS and len(token) > 2}
            if len(aliases) > 128:
                raise ConceptBlocked('LOOKUP_BUDGET')
            for size in range(2, 7):
                for start in range(len(tokens) - size + 1):
                    if len(aliases) >= 128:
                        break
                    aliases.add(normalize(' '.join(tokens[start:start + size])))
        rows = release.lookup(aliases, max_records=self.max_records, budget_ms=self.lookup_ms)
        if not rows:
            return self.base
        merged = dict(self.base.concepts)
        for row in rows:
            concept = Concept.model_validate(row)
            if (concept.review_status != 'approved' or any(t.review_status != 'approved' for t in concept.terms)
                    or concept.relations):
                raise ConceptBlocked('UNREVIEWED_RUNTIME_CONCEPT')
            # Existing seed identity/definition wins, rather than accepting an
            # artifact overwrite of the curated source or its approval history.
            existing = merged.get(concept.concept_id)
            if existing is not None:
                if concept.domain != existing.domain:
                    raise ConceptBlocked('SEED_DOMAIN_CONFLICT')
                keys = {(term.language, term.normalized) for term in existing.terms}
                additions = [term for term in concept.terms if (term.language, term.normalized) not in keys]
                merged[concept.concept_id] = existing.model_copy(update={'terms': [*existing.terms, *additions]})
            else:
                merged[concept.concept_id] = concept
        snapshot = Snapshot(version='dynamic:' + release.manifest['generation'], concepts=list(merged.values()))
        dictionary = Dictionary(snapshot)
        dictionary.fingerprint = digest([self.base.fingerprint, release.manifest['generation'], snapshot.fingerprint])
        dictionary.visibility_guard = lambda: self._valid(release)
        weight = len(snapshot.model_dump_json().encode('utf-8')) * 8
        if weight <= self.cache_bytes:
            with self._lock:
                # Another concurrent lookup may have filled the same key.
                old = self._cache.pop(key, None)
                if old:
                    self._bytes -= old[1]
                while self._cache and (self._bytes + weight > self.cache_bytes or len(self._cache) >= 128):
                    _, (_, removed) = self._cache.popitem(last=False)
                    self._bytes -= removed
                self._cache[key] = dictionary, weight
                self._bytes += weight
        return dictionary

    @contextmanager
    def pin(self, query, domain=None):
        with self._lock:
            release = self._active
            retained = bool(self.enabled and self._valid(release))
            if retained:
                self._retain_locked(release)
        dictionary, used = self.base, None
        started = monotonic()
        if self.enabled and self._valid(release) and isinstance(query, str):
            try:
                dictionary = self._resolve(release, query, domain)
                if not self._valid(release):
                    dictionary = self.base
                elif dictionary is not self.base:
                    used = release
            except Exception:
                self.metrics['fallbacks'] += 1
        elapsed = (monotonic() - started) * 1000
        with self._lock:
            self.metrics['lookups'] += 1
            self.metrics['lookup_ms_total'] += elapsed
            self._latencies.append(elapsed)
        try:
            yield dictionary
            if used is not None and not self._valid(used):
                # Caller must discard response and recompute once using literal/seed.
                raise ConceptBlocked('DYNAMIC_REVOKED')
        finally:
            if retained:
                with self._lock:
                    self._release_locked(release)

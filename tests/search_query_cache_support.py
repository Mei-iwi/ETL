"""Attested deterministic encoder DOUBLE for isolated cache tests only."""
import hashlib

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.indexing import IndexingError


class EncoderIdentity:
    pass


class Runtime384:
    model_id, version, dimension = 'cache-test-double-only', 'fixture-revision', 384

    def __init__(self):
        self.encodes = self.prepares = self.loads = 0
        self.enabled = True
        self.checked = False
        self._model = None

    def available(self):
        return self.enabled

    def ready(self):
        return self.enabled and self.checked

    def prepare(self):
        self.prepares += 1
        if not self.enabled:
            raise IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503)
        if not self.checked:
            self.loads += 1
            self._model = EncoderIdentity()
            self.checked = True

    def _encode(self, text):
        self.encodes += 1
        digest = hashlib.sha256(text.encode('utf-8')).digest()
        return [(digest[i % len(digest)] + 1) / 256 for i in range(self.dimension)]

    def prepare_query(self):
        self.prepare()
        return self._encode

    def embed(self, text):
        return self.prepare_query()(text)


def configure(c, version, runtime=None):
    runtime = runtime or Runtime384()
    c.settings.search_query_embedding_cache_enabled = True
    c.indexing.embedding = runtime
    c.settings.index_embedding_dimension = c.indexing.bags.dimension = 384
    c.indexing.enqueue(version['id'], force=True)
    assert c.indexing.once()
    c.search = SearchService(c.indexing, c.settings, c.indexing.bags, query_cache_runtime=runtime)
    return runtime

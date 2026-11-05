"""Bounded memoization of pure embeddings, never text, identities or authority."""

from collections import OrderedDict
from hashlib import sha256
from os import replace
from pathlib import Path
from struct import pack, unpack_from
from tempfile import NamedTemporaryFile
from threading import Lock

from data_sync_etl.domain.indexing import IndexingError, digest, validate_vector
from data_sync_etl.logging import event


class ExactTextVectors:
    MAGIC = b'ETL-BGE1'
    RECORD_BYTES = 32 + 1024 * 8

    def __init__(self, embedding, capacity=2048, *, cache_file=None):
        self.embedding, self.capacity = embedding, capacity
        self._vectors, self._lock, self._save_lock = OrderedDict(), Lock(), Lock()
        self.cache_file = Path(cache_file) if cache_file else None
        self._restore()

    def _restore(self):
        path = self.cache_file
        if path is None:
            return
        try:
            if path.is_symlink() or path.stat().st_size > 44 + self.capacity * self.RECORD_BYTES:
                return
            raw = path.read_bytes()
            payload = raw[32:]
            if len(payload) < 12 or sha256(payload).digest() != raw[:32] or payload[:8] != self.MAGIC:
                return
            count = unpack_from('<I', payload, 8)[0]
            if count > self.capacity or len(payload) != 12 + count * self.RECORD_BYTES:
                return
            restored = OrderedDict()
            for index in range(count):
                offset = 12 + index * self.RECORD_BYTES
                key = payload[offset:offset + 32].hex()
                vector = unpack_from('<1024d', payload, offset + 32)
                restored[key] = tuple(validate_vector(vector, 1024))
            self._vectors = restored
        except (OSError, ValueError, IndexingError):
            pass  # Disposable derived artifact: recompute, never use partial data.

    def _save(self):
        if self.cache_file is None:
            return
        temporary = None
        try:
            with self._save_lock:
                with self._lock:
                    records = list(self._vectors.items())
                payload = self.MAGIC + pack('<I', len(records)) + b''.join(
                    bytes.fromhex(key) + pack('<1024d', *vector) for key, vector in records)
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=self.cache_file.parent, prefix='.bge-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(sha256(payload).digest() + payload)
                replace(temporary, self.cache_file)
        except OSError:
            pass  # Storage failure only disables restart reuse, not live checks.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def encode(self, texts, *, checkpoint=lambda: None):
        identity = self.embedding.model_identity()
        # Availability is checked even when every derived vector is memoized.
        self.embedding.runtime.require_search_ready()
        keys = [digest(['exact-text-bge-v1', identity, text]) for text in texts]
        found, missing = {}, {}
        with self._lock:
            for key, text in zip(keys, texts, strict=True):
                if key in self._vectors:
                    found[key] = self._vectors[key]
                    self._vectors.move_to_end(key)
                else:
                    missing.setdefault(key, text)
        pending = list(missing)
        event(stage='normalized_search', action='VECTOR_MEMO', coverage={
            'inputs': len(texts), 'unique_missing': len(pending), 'unique_hits': len(found)})
        for offset in range(0, len(pending), 16):
            checkpoint()
            batch = pending[offset:offset + 16]
            rows = self.embedding.embed_documents([missing[key] for key in batch])
            if len(rows) != len(batch):
                raise IndexingError('INVALID_EMBEDDING', 503)
            for key, row in zip(batch, rows, strict=True):
                found[key] = tuple(validate_vector(row['vector'], 1024))
            if self.embedding.model_identity() != identity:
                raise IndexingError('SEMANTIC_MODEL_IDENTITY_CHANGED', 503)
            with self._lock:
                for key in batch:
                    self._vectors[key] = found[key]
                while len(self._vectors) > self.capacity:
                    self._vectors.popitem(last=False)
            self._save()
        if self.embedding.model_identity() != identity:
            raise IndexingError('SEMANTIC_MODEL_IDENTITY_CHANGED', 503)
        # Each memoized batch is a completely validated pure model computation.
        # Interrupted cold preparation never publishes a partial ranked corpus.
        # No query/result, ID, text, permission or database row is retained.
        return [list(found[key]) for key in keys]

"""Offline-only FlagEmbedding BGE-M3. One lazily loaded model per container.

    No downloads, no random fallback, no BGE Sparse on the production path.
    Deadlines are cooperative: running native inference cannot be killed safely.
"""
import importlib.util
import json
import re
from pathlib import Path
from threading import Lock, RLock, Thread
from time import monotonic
from collections.abc import Callable
from typing import Any

from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.semantic import normalize_dense
from data_sync_etl.ports.metadata_search import QUERY_ENCODINGS


class BgeM3Embedding:
    dimension = 1024
    model_id = 'BAAI/bge-m3'

    def __init__(self, path, revision, *, device='cpu', batch_size=2,
                 max_tokens=1024, timeout_seconds=300):
        self.path = Path(path) if path else None
        self.model_revision = revision
        self.device, self.batch_size = device, batch_size
        self.max_tokens, self.timeout_seconds = max_tokens, timeout_seconds
        self._model, self._lock = None, RLock()
        self._lifecycle_lock, self._thread = Lock(), None
        self._state, self._stopped, self._retry_used = 'NOT_STARTED', False, False
        self._loaded_identity = None

    def available(self):
        return self.is_available()

    def ready(self):
        return self._model is not None and self._loaded_identity == self.model_identity()

    def search_status(self):
        with self._lifecycle_lock:
            return {'state': self._state, 'ready': self._state == 'READY'}

    def start_preload(self):
        with self._lifecycle_lock:
            if self._thread is not None or self._stopped:
                return
            self._start()

    def _start(self):
        self._state = 'LOADING'
        self._thread = Thread(target=self._preload, name='etl-bge-m3-preload', daemon=True)
        self._thread.start()

    def _preload(self):
        state = 'READY'
        try:
            self.embed_queries(['model readiness check'])
        except Exception:
            state = 'FAILED'
        with self._lifecycle_lock:
            self._state = 'STOPPED' if self._stopped else state

    def retry_preload(self):
        with self._lifecycle_lock:
            if self._stopped or self._retry_used or self._state != 'FAILED':
                return False
            self._retry_used = True
            self._start()
            return True

    def request_stop(self):
        with self._lifecycle_lock:
            self._stopped, self._state = True, 'STOPPED'

    def stop_preload(self):
        self.request_stop()
        with self._lifecycle_lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=1)

    def require_search_ready(self):
        if self._thread is not None and self.search_status()['state'] != 'READY':
            from data_sync_etl.domain.search import SearchError
            raise SearchError('SEARCH_MODEL_LOADING' if self._state == 'LOADING' else 'SEMANTIC_MODEL_UNAVAILABLE')

    def model_identity(self):
        return {'model_id': self.model_id, 'revision': self.model_revision,
                'dimension': self.dimension, 'engine': 'FlagEmbedding-1.3.5',
                'max_tokens': self.max_tokens, 'device': self.device,
                'precision': 'fp32', 'normalization': 'l2-v1', 'overflow': 'reject-v1'}

    def _local_contract(self):
        try:
            path = self.path.resolve(strict=True)
            if not re.fullmatch(r'[a-f0-9]{40}', self.model_revision):
                return False
            if (path / 'etl-model-revision.txt').read_text().strip() != self.model_revision:
                return False
            config = json.loads((path / 'config.json').read_text())
            return (config['hidden_size'] == self.dimension
                    and (path / 'tokenizer.json').is_file()
                    and any((path / name).is_file() for name in ('model.safetensors', 'pytorch_model.bin'))
                    and (path / 'colbert_linear.pt').is_file()
                    and (path / 'sparse_linear.pt').is_file())
        except (OSError, ValueError, KeyError, AttributeError):
            return False

    def is_available(self):
        return self._local_contract() and importlib.util.find_spec('FlagEmbedding') is not None

    def prepare(self):
        with self._lock:
            if not self.is_available():
                raise IndexingError('SEMANTIC_MODEL_UNAVAILABLE', 503)
            if self._model is not None:
                if self._loaded_identity != self.model_identity():
                    raise IndexingError('SEMANTIC_MODEL_IDENTITY_CHANGED', 503)
                return
            try:
                from FlagEmbedding import BGEM3FlagModel
                # An absolute, verified local directory is passed instead of a Hub ID.
                # FlagEmbedding 1.3.5 internally resolves tokenizer and weights here.
                self._model = BGEM3FlagModel(str(self.path.resolve()), devices=self.device,
                    use_fp16=False, normalize_embeddings=True, trust_remote_code=False,
                    batch_size=self.batch_size, query_max_length=self.max_tokens,
                    passage_max_length=self.max_tokens)
                self._loaded_identity = self.model_identity()
            except Exception:
                self._model = None
                raise IndexingError('SEMANTIC_MODEL_UNAVAILABLE', 503) from None

    def _encode(self, texts, *, colbert=False):
        if not texts:
            return []
        if len(texts) > self.batch_size or any(not text.strip() or len(text) > 100000 for text in texts):
            raise IndexingError('SEMANTIC_BATCH_LIMIT', 422)
        with self._lock:
            self.prepare()
            started = monotonic()
            try:
                counts = [len(self._model.tokenizer.encode(text, truncation=False)) for text in texts]
                if any(count > self.max_tokens for count in counts):
                    # Preserve raw text and offsets; operators can increase the token budget
                    # or choose a separately versioned chunk policy rather than lose content.
                    raise IndexingError('SEMANTIC_TOKEN_LIMIT', 422)
                encoded = self._model.encode(texts, batch_size=self.batch_size,
                    max_length=self.max_tokens, return_dense=True, return_sparse=False,
                    return_colbert_vecs=colbert)
                vectors = encoded['dense_vecs'].tolist()
                if len(vectors) != len(texts):
                    raise IndexingError('INVALID_EMBEDDING', 503)
                result = [{'vector': normalize_dense(vector), 'token_count': count,
                           'truncated': False} for vector, count in zip(vectors, counts, strict=True)]
                if colbert:
                    for row, tokens in zip(result, encoded['colbert_vecs'], strict=True):
                        if not len(tokens):
                            raise IndexingError('INVALID_EMBEDDING', 503)
                        row['colbert'] = tokens
            except IndexingError:
                raise
            except Exception:
                raise IndexingError('SEMANTIC_INFERENCE_FAILED', 503) from None
            if monotonic() - started > self.timeout_seconds:
                raise IndexingError('SEMANTIC_INFERENCE_TIMEOUT', 503)
            return result

    def embed_documents(self, texts):
        return self._encode(texts)

    def embed_query_windows(self, query: str, *, max_windows: int = 3,
                            checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
        """Opt-in full-query pooling, with a hard budget and no token truncation.

        Split only at whitespace; never cut identifiers or remove a trailing
        condition. Validate ALL windows before encoding any of them.
        """
        if not 1 <= max_windows <= 3 or not query.strip() or len(query) > 4000:
            raise IndexingError('SEMANTIC_QUERY_WINDOW_LIMIT', 422)
        with self._lock:
            self.prepare()
            pending, windows = [query], []
            while pending:
                checkpoint()
                text = pending.pop(0)
                count = len(self._model.tokenizer.encode(text, truncation=False))
                if count <= self.max_tokens:
                    windows.append(text)
                else:
                    spaces = list(re.finditer(r'\s+', text))
                    if not spaces:
                        raise IndexingError('SEMANTIC_TOKEN_LIMIT', 422)
                    split = min(spaces, key=lambda match: abs(match.start() - len(text) / 2))
                    pending[0:0] = [text[:split.start()], text[split.end():]]
                if len(windows) + len(pending) > max_windows:
                    raise IndexingError('SEMANTIC_QUERY_WINDOW_LIMIT', 422)
            outputs = []
            for offset in range(0, len(windows), self.batch_size):
                checkpoint()
                outputs.extend(self._encode(windows[offset:offset + self.batch_size]))
            checkpoint()
            total = sum(row['token_count'] for row in outputs)
            vector = normalize_dense([sum(row['vector'][i] * row['token_count'] for row in outputs) / total
                                      for i in range(self.dimension)])
            return {'vector': vector, 'token_count': total, 'truncated': False, 'query_windows': len(windows)}

    def embed_queries(self, queries):
        scope = QUERY_ENCODINGS.get()
        if scope is None or len(queries) != 1:
            return self._encode(queries)
        from data_sync_etl.domain.indexing import digest

        self.prepare()
        key = (id(self), digest([self.model_identity(), queries]))
        if key not in scope:
            result = self._encode(queries)
            if len(scope) < 4:
                scope[key] = [{**row, 'vector': list(row['vector'])} for row in result]
            return result
        return [{**row, 'vector': list(row['vector'])} for row in scope[key]]

    def rerank(self, query, texts):
        """Mean query-token MaxSim, matching BGE-M3 ColBERT; never a cross encoder."""
        import numpy as np
        if not 1 <= len(texts) <= 50:
            raise IndexingError('SEMANTIC_RERANK_LIMIT', 422)
        with self._lock:
            query_tokens = self._encode([query], colbert=True)[0]['colbert']
            scores, started = [], monotonic()
            for offset in range(0, len(texts), self.batch_size):
                for row in self._encode(texts[offset:offset + self.batch_size], colbert=True):
                    score = float((query_tokens @ row['colbert'].T).max(axis=1).mean())
                    if not np.isfinite(score):
                        raise IndexingError('INVALID_EMBEDDING', 503)
                    scores.append(score)
                if monotonic() - started > self.timeout_seconds:
                    raise IndexingError('SEMANTIC_INFERENCE_TIMEOUT', 503)
            return scores

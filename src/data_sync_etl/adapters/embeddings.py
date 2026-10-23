"""Local-only resident embedding runtime with bounded background startup."""

import json
import logging
import os
from importlib.util import find_spec
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from data_sync_etl.domain.indexing import IndexingError, digest, validate_vector


class LocalSentenceEmbedding:
    def __init__(self, path, model_id, version, dimension):
        self.path = path
        self.model_id = model_id
        self.version = version
        self.dimension = dimension
        self._model = None
        self._lock = Lock()
        self._checked = False
        self._loaded_contract = None
        self._lifecycle_lock = Lock()
        self._stop = Event()
        self._preload_thread = None
        self._retry_used = False
        self._state = 'UNINITIALIZED'
        self._load_count = 0
        self._load_seconds = self._warmup_seconds = self._startup_seconds = 0.0

    def start_preload(self):
        """One bounded task per shared adapter/process; never download weights."""
        with self._lifecycle_lock:
            if self._preload_thread is not None:
                return
            self._state = 'LOADING'
            self._preload_thread = Thread(target=self._preload, name='etl-model-preload', daemon=True)
            self._preload_thread.start()

    def retry_preload(self):
        """One explicit recovery round, only after the previous task exited."""
        with self._lifecycle_lock:
            if (self._stop.is_set() or self._retry_used or self._preload_thread is None
                    or self._preload_thread.is_alive() or (self._state != 'FAILED' and self._checked)):
                return False
            self._retry_used = True
            self._state = 'LOADING'
            self._preload_thread = Thread(target=self._preload, name='etl-model-preload', daemon=True)
            self._preload_thread.start()
            return True

    def _preload(self):
        started = monotonic()
        for attempt in range(2):
            if self._stop.is_set():
                return
            try:
                self.prepare()
            except Exception:
                # One retry; no raw adapter exceptions, paths or query content.
                if attempt == 0 and not self._stop.wait(2):
                    continue
                with self._lifecycle_lock:
                    self._state = 'STOPPED' if self._stop.is_set() else 'FAILED'
                logging.getLogger('etl').warning('search_model FAILED pid=%s attempts=%s', os.getpid(), attempt + 1)
                return
            with self._lifecycle_lock:
                self._state = 'STOPPED' if self._stop.is_set() else 'READY'
                self._startup_seconds = monotonic() - started
            logging.getLogger('etl').info('search_model %s pid=%s startup_seconds=%.3f',
                                          self._state, os.getpid(), self._startup_seconds)
            return

    def stop_preload(self):
        # Native torch loading cannot be force-killed safely. Stop retry and
        # bound shutdown waiting; at most ONE daemon may finish its native call.
        self._stop.set()
        with self._lifecycle_lock:
            self._state = 'STOPPED'
            thread = self._preload_thread
        if thread is not None:
            thread.join(timeout=1)

    def search_status(self):
        """Read-only, process-local snapshot: no filesystem, model load or encode."""
        with self._lifecycle_lock:
            state = self._state
            if state == 'READY' and not self._checked:
                state = 'FAILED'
            return {'state': state, 'ready': state == 'READY'}

    def require_search_ready(self):
        # Direct workers retain their existing lazy prepare API. Managed HTTP
        # never holds the T3 inference queue waiting for cold initialization.
        if self._preload_thread is not None:
            state = self.search_status()['state']
            if state != 'READY':
                from data_sync_etl.domain.search import SearchError
                raise SearchError('SEARCH_MODEL_LOADING' if state == 'LOADING' else 'BLOCKED_EMBEDDING_RUNTIME')

    def available(self):
        if not (self.path and self.model_id.strip() and self.version.strip()
                and Path(self.path).is_dir() and find_spec("sentence_transformers")):
            return False
        try:
            root = Path(self.path).resolve()
            manifest = root / "etl-model-revision.txt"
            modules = json.loads((root / "modules.json").read_text(encoding="utf-8"))
            return (manifest.read_text(encoding="utf-8").strip() == self.version
                    and bool(modules) and (any(root.rglob("*.safetensors")) or (root / "pytorch_model.bin").is_file())
                    and all((root / module.get("path", "")).resolve().is_relative_to(root) for module in modules))
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def prepare(self):
        if not self.available():
            raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503)
        with self._lock:
            contract = (str(Path(self.path).resolve()), self.model_id, self.version, self.dimension)
            if self._loaded_contract is not None and contract != self._loaded_contract:
                raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503)
            if self._model is None:
                try:
                    started = monotonic()
                    from sentence_transformers import SentenceTransformer

                    model = SentenceTransformer(str(Path(self.path).resolve()),
                                                local_files_only=True, device="cpu")
                    if model.get_sentence_embedding_dimension() != self.dimension:
                        raise IndexingError("INVALID_EMBEDDING", 503)
                    # Configured revision must match the operator's provisioned manifest.
                    manifest = Path(self.path) / "etl-model-revision.txt"
                    if not manifest.is_file() or manifest.read_text(encoding="utf-8").strip() != self.version:
                        raise ValueError("Model revision unavailable")
                    self._model = model
                    self._loaded_contract = contract
                    self._load_count += 1
                    self._load_seconds = monotonic() - started
                except IndexingError:
                    raise
                except Exception:
                    raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503) from None
            if not self._checked:
                started = monotonic()
                self._encode("Kiểm tra mô hình ngữ nghĩa")
                self._checked = True
                self._warmup_seconds = monotonic() - started

    def ready(self):
        # Readiness check never loads or probes a new model.
        return (self._checked and self.available()
                and self._loaded_contract == (str(Path(self.path).resolve()), self.model_id, self.version, self.dimension))

    def identity(self):
        # No local path in public contracts, fingerprints or logs.
        return digest([self.model_id, self.version, self.dimension])

    def embed(self, text):
        self.prepare()
        return self._encode(text)

    def embed_many(self, texts):
        if not isinstance(texts, list) or not 1 <= len(texts) <= 16 or any(not isinstance(text, str) for text in texts):
            raise IndexingError('INVALID_EMBEDDING', 503)
        self.prepare()
        try:
            vectors = self._model.encode(texts, batch_size=16, show_progress_bar=False).tolist()
        except Exception:
            self._checked = False
            raise IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503) from None
        if len(vectors) != len(texts):
            raise IndexingError('INVALID_EMBEDDING', 503)
        return [validate_vector(vector, self.dimension) for vector in vectors]

    def prepare_query(self):
        """Validate/load once, then return the encoder for the immediate query.

        Search times preparation separately from bounded query execution. The
        returned callable is request-local; direct embed() still prepares itself.
        No cross-request vector or readiness cache is retained here.
        """
        self.prepare()
        return self._encode

    def _encode(self, text):
        try:
            vector = self._model.encode(text, show_progress_bar=False).tolist()
        except Exception:
            self._checked = False
            raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503) from None
        return validate_vector(vector, self.dimension)

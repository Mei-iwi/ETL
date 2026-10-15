"""Optional, local-only runtime. Construction/startup never downloads or loads a model."""

from importlib.util import find_spec
from pathlib import Path
from threading import Lock

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

    def available(self):
        return bool(self.path and self.model_id.strip() and self.version.strip()
                    and Path(self.path).is_dir() and find_spec("sentence_transformers"))

    def prepare(self):
        if not self.available():
            raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503)
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer

                    model = SentenceTransformer(str(Path(self.path).resolve()),
                                                local_files_only=True, device="cpu")
                    if model.get_sentence_embedding_dimension() != self.dimension:
                        raise ValueError("Dimension mismatch")
                    # Configured revision must match the operator's provisioned manifest.
                    manifest = Path(self.path) / "etl-model-revision.txt"
                    if not manifest.is_file() or manifest.read_text(encoding="utf-8").strip() != self.version:
                        raise ValueError("Model revision unavailable")
                    self._model = model
                except Exception:
                    raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503) from None
            if not self._checked:
                validate_vector(self._model.encode("Kiểm tra mô hình ngữ nghĩa").tolist(), self.dimension)
                self._checked = True

    def ready(self):
        return self._checked and self.available()

    def identity(self):
        # No local path in public contracts, fingerprints or logs.
        return digest([self.model_id, self.version, self.dimension])

    def embed(self, text):
        self.prepare()
        return validate_vector(self._model.encode(text, show_progress_bar=False).tolist(), self.dimension)

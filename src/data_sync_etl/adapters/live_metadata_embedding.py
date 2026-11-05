"""Request-local title vectors using the existing attested offline BGE runtime."""
from data_sync_etl.domain.indexing import IndexingError, validate_vector


class LiveMetadataEmbedding:
    def __init__(self, runtime):
        self.runtime = runtime

    def model_identity(self):
        runtime = self.runtime
        if (runtime.dimension != 1024 or runtime.model_id.lower() not in {'baai/bge-m3', 'bge-m3'}
                or not runtime.version.strip()):
            raise IndexingError('INVALID_EMBEDDING', 503)
        return {'model_id': runtime.model_id, 'revision': runtime.version, 'dimension': 1024,
                'engine': 'resident-sentence-transformer', 'representation': 'metadata-title-v1'}

    def _encode(self, texts):
        self.model_identity()
        if not isinstance(texts, list) or not 1 <= len(texts) <= 16:
            raise IndexingError('INVALID_EMBEDDING', 503)
        self.runtime.require_search_ready()
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise IndexingError('INVALID_EMBEDDING', 503)
            self.runtime.validate_query(text)  # No silent tokenizer truncation.
        vectors = self.runtime.embed_many(texts)
        if len(vectors) != len(texts):
            raise IndexingError('INVALID_EMBEDDING', 503)
        return [{'vector': validate_vector(vector, 1024)} for vector in vectors]

    embed_documents = _encode
    embed_queries = _encode

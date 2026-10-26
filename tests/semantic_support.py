"""Isolated test doubles; never imported by application runtime."""
from copy import deepcopy

from data_sync_etl.domain.indexing import IndexingError


class MemorySemanticStore:
    def __init__(self):
        self.manifests, self.rows = {}, {}

    def ensure_ready(self):
        pass

    def manifest(self, key):
        return deepcopy(self.manifests.get(key))

    def has_ready_version(self, version_id):
        return any(row.get('resource_version_id') == version_id and row.get('status') == 'READY'
                   for row in self.manifests.values())

    def claim(self, plan, lease_seconds):
        if self.manifests.get(plan['_id'], {}).get('status') == 'RUNNING':
            raise IndexingError('SEMANTIC_INDEX_BUSY', 409)
        self.manifests[plan['_id']] = {**deepcopy(plan), 'status': 'RUNNING'}
        return 'test-owner'

    def records(self, key, limit):
        return deepcopy(sorted([row for row in self.rows.values() if row['index_id'] == key],
                               key=lambda row: row['chunk_id'])[:limit])

    def save(self, key, owner, record):
        self.rows.setdefault(record['_id'], deepcopy(record))

    def finish(self, key, owner, status, code=None):
        self.manifests[key].update(status=status, error_code=code)


class SemanticModelDouble:
    dimension, model_revision, batch_size = 1024, 'a' * 40, 2

    def __init__(self):
        self.inputs, self.calls, self.fail_after = [], 0, None

    def model_identity(self):
        return {'model_id': 'TEST_DOUBLE_NOT_REAL_BGE', 'revision': self.model_revision, 'dimension': 1024}

    def prepare(self):
        pass

    def require_search_ready(self):
        pass

    def embed_documents(self, texts):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise IndexingError('SEMANTIC_INFERENCE_FAILED', 503)
        self.inputs.extend(texts)
        return [{'vector': [1.] + [0.] * 1023, 'token_count': len(text.split()), 'truncated': False}
                for text in texts]

    embed_queries = embed_documents

    def rerank(self, query, texts):
        return [float(index) for index, _ in enumerate(texts, 1)]

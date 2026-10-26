"""Chunk indexing and bounded retrieval; no writes on the Search path.

    The original projection, bag membership and word-window context locators are
    reused. Separate immutable semantic generations preserve all legacy vectors.
"""
import math
from datetime import UTC, datetime
from time import monotonic

from data_sync_etl.application.content_bags import bag_structure
from data_sync_etl.domain.evidence_selection import EvidenceCandidate
from data_sync_etl.domain.indexing import IndexingError, digest
from data_sync_etl.domain.semantic import (
    CONTEXT_FORMAT,
    context_headings,
    diverse_candidates,
    embedding_input,
    normalize_dense,
    record_identity,
)
from data_sync_etl.ports.semantic import SemanticEmbedding, SemanticStore


class SemanticService:
    def __init__(self, indexing, embedding: SemanticEmbedding, store: SemanticStore, settings):
        self.indexing, self.embedding, self.store, self.settings = indexing, embedding, store, settings

    def plan(self, snapshot, title):
        metadata = title if isinstance(title, dict) else {'title': title}
        strategy = self.settings.semantic_strategy
        identity = self.embedding.model_identity()
        # Title is independently included: the legacy snapshot does not hash it.
        fingerprint = digest([snapshot[3], metadata, identity, strategy, CONTEXT_FORMAT])
        roots = bag_structure(snapshot[2])
        leaf_root = {leaf: root for root, leaves in roots.items() for leaf in leaves}
        records = []
        prepared = []
        for chunk in self.indexing._preview(snapshot):
            root = leaf_root.get(chunk['content_unit_id'])
            if root is None:
                continue
            text = embedding_input(chunk, title=metadata.get('title'), strategy=strategy,
                subjects=metadata.get('subjects', ()), grades=metadata.get('grades', ()),
                headings=context_headings(snapshot[2], chunk['content_unit_id']))
            prepared.append((chunk, root, text))
        if self.settings.semantic_input_identity_enabled:
            fingerprint = digest([snapshot[3], identity, strategy, CONTEXT_FORMAT,
                [(chunk['id'], digest(text)) for chunk, _, text in prepared]])
        for chunk, root, text in prepared:
            records.append({
                '_id': record_identity(fingerprint, chunk['id']), 'index_id': fingerprint,
                'chunk_id': chunk['id'], 'content_unit_id': chunk['content_unit_id'],
                'bag_id': root, 'resource_id': snapshot[0]['resource_id'],
                'resource_version_id': snapshot[0]['id'], 'projection_generation': snapshot[1],
                'source_fingerprint': snapshot[3], 'raw_hash': digest(chunk['text']),
                'input_hash': digest(text), 'embedding_text': text, 'model_identity': identity,
                'strategy': strategy, 'context_format': CONTEXT_FORMAT, 'dimension': 1024,
                **{key: chunk[key] for key in ('chunk_sequence', 'unit_chunk_sequence',
                    'page_from', 'page_to', 'char_start', 'char_end')},
            })
        if not records or len(records) > self.settings.semantic_max_candidates:
            raise IndexingError('SEMANTIC_INDEX_LIMIT', 422)
        return {'_id': fingerprint, 'resource_version_id': snapshot[0]['id'],
                'source_fingerprint': snapshot[3], 'model_identity': identity,
                'strategy': strategy, 'chunk_count': len(records)}, records

    def _snapshot(self, version_id):
        with self.indexing.uow() as repo:
            snapshot = self.indexing._snapshot(repo, version_id)
            metadata = repo.semantic_metadata(snapshot[0]['resource_id'])
        return snapshot, metadata

    @staticmethod
    def _validated(rows, expected):
        actual = {row['chunk_id']: row for row in rows}
        if len(actual) != len(rows) or actual.keys() != {row['chunk_id'] for row in expected}:
            raise IndexingError('SEMANTIC_INDEX_INCOMPLETE', 409)
        for spec in expected:
            row = actual[spec['chunk_id']]
            if any(row.get(key) != value for key, value in spec.items()) or row.get('status') != 'READY':
                raise IndexingError('SEMANTIC_INDEX_STALE', 409)
            vector = normalize_dense(row.get('vector', []))
            if any(abs(a - b) > 1e-5 for a, b in zip(vector, row['vector'], strict=True)):
                raise IndexingError('INVALID_EMBEDDING', 503)
            # Retain the normalization already computed for validation. Work on a
            # copy: never mutate stored vectors, generation identity or inputs.
            actual[spec['chunk_id']] = {**row, 'vector': vector}
        return actual

    def build(self, version_id, *, apply=False):
        snapshot, title = self._snapshot(version_id)
        plan, expected = self.plan(snapshot, title)
        result = {'index_id': plan['_id'], 'chunk_count': len(expected),
                  'strategy': plan['strategy'], 'model_identity': plan['model_identity']}
        if not apply:
            return {**result, 'status': 'DRY_RUN'}
        self.store.ensure_ready()
        self.embedding.prepare()
        existing = self.store.manifest(plan['_id'])
        if existing and existing['status'] == 'READY':
            self._validated(self.store.records(plan['_id'], len(expected) + 1), expected)
            return {**result, 'status': 'READY', 'action': 'UNCHANGED'}
        owner = self.store.claim(plan, self.settings.index_timeout_seconds + 60)
        started = monotonic()
        def deadline():
            if monotonic() - started > self.settings.index_timeout_seconds:
                raise IndexingError('SEMANTIC_INDEX_TIMEOUT', 503)
        try:
            rows = self.store.records(plan['_id'], len(expected) + 1)
            present = {row['chunk_id']: row for row in rows}
            # Validate persisted partial rows before resume, including model/input identity.
            for spec in expected:
                if spec['chunk_id'] in present:
                    self._validated([present[spec['chunk_id']]], [spec])
            pending = [row for row in expected if row['chunk_id'] not in present]
            for offset in range(0, len(pending), self.embedding.batch_size):
                deadline()
                batch = pending[offset:offset + self.embedding.batch_size]
                encoded = self.embedding.embed_documents([row['embedding_text'] for row in batch])
                deadline()
                if len(encoded) != len(batch):
                    raise IndexingError('INVALID_EMBEDDING', 503)
                for row, output in zip(batch, encoded, strict=True):
                    if output['truncated']:
                        raise IndexingError('SEMANTIC_TOKEN_LIMIT', 422)
                    self.store.save(plan['_id'], owner, {**row, 'status': 'READY',
                        'vector': normalize_dense(output['vector']), 'token_count': output['token_count'],
                        'truncated': False, 'created_at': datetime.now(UTC)})
            fresh, fresh_title = self._snapshot(version_id)
            if self.plan(fresh, fresh_title)[0] != plan:
                raise IndexingError('SEMANTIC_INDEX_STALE', 409)
            self._validated(self.store.records(plan['_id'], len(expected) + 1), expected)
            deadline()
            self.store.finish(plan['_id'], owner, 'READY')
        except Exception as exc:
            code = exc.code if isinstance(exc, IndexingError) else 'SEMANTIC_INDEX_FAILED'
            try:
                self.store.finish(plan['_id'], owner, 'FAILED', code)
            except IndexingError:
                pass  # Preserve original failure if lease ownership changed.
            raise IndexingError(code, 503) from None
        return {**result, 'status': 'READY', 'action': 'BUILT'}

    def query(self, query):
        self.store.ensure_ready()
        return normalize_dense(self.embedding.embed_queries([query])[0]['vector'])

    def status(self, version_id):
        """Read-only coverage/identity check; does not load the model or queue work."""
        snapshot, title = self._snapshot(version_id)
        plan, expected = self.plan(snapshot, title)
        self.store.ensure_ready()
        manifest = self.store.manifest(plan['_id'])
        rows = self.store.records(plan['_id'], len(expected) + 1)
        state = manifest['status'] if manifest else 'NOT_STARTED'
        code = manifest.get('error_code') if manifest else None
        if manifest is None and self.store.has_ready_version(version_id):
            state, code = 'STALE', 'SEMANTIC_INDEX_STALE'
        if state == 'READY':
            self._validated(rows, expected)
        return {'index_id': plan['_id'], 'status': state, 'error_code': code,
                'expected_chunks': len(expected), 'persisted_chunks': len(rows),
                'coverage_verified': state == 'READY', 'model_identity': plan['model_identity'],
                'strategy': plan['strategy']}

    def refresh_existing(self, version_id):
        """Selective re-embed only pre-existing semantic generations; never OCR."""
        if not self.store.has_ready_version(version_id):
            return {'status': 'NOT_INDEXED', 'action': 'SKIPPED'}
        status = self.status(version_id)
        if status['status'] == 'READY':
            return {**status, 'action': 'UNCHANGED'}
        return self.build(version_id, apply=True)

    def scores(self, snapshot, title, roots, allowed, vector, deadline, budget, *, evidence_candidates=None,
               query_vectors=()):
        """Bounded exact cosine over the filtered versions, no unbounded corpus scan.

        This first implementation is exact CPU retrieval, not a Mongo Atlas ANN
        claim. A request-wide hard candidate cap requires narrower filters if hit.
        Only authorized live chunks may contribute scores or enter reranking.
        """
        if not roots or not allowed:
            return {}  # An inaccessible version cannot make an allowed search unavailable.
        if len(query_vectors) > 1:
            raise IndexingError('SEMANTIC_QUERY_VARIANT_LIMIT', 422)
        plan, expected = self.plan(snapshot, title)
        manifest = self.store.manifest(plan['_id'])
        if not manifest or manifest.get('status') != 'READY':
            if manifest is None and self.store.has_ready_version(snapshot[0]['id']):
                raise IndexingError('SEMANTIC_INDEX_STALE', 409)
            raise IndexingError('SEMANTIC_INDEX_NOT_READY', 503)
        if manifest.get('chunk_count') != len(expected):
            raise IndexingError('SEMANTIC_INDEX_INCOMPLETE', 409)
        budget[0] += len(expected)
        if budget[0] > self.settings.semantic_max_candidates:
            raise IndexingError('SEMANTIC_CANDIDATE_LIMIT', 422)
        rows = self._validated(self.store.records(plan['_id'], len(expected) + 1), expected)
        query_vectors = [normalize_dense(value) for value in [vector, *query_vectors]]
        chunks = {chunk['id']: chunk for chunk in self.indexing._preview(snapshot)}
        best = {}
        for spec in expected:
            deadline()
            root, unit = spec['bag_id'], spec['content_unit_id']
            if root not in roots or unit not in allowed:
                continue
            # Both operands have passed the same dimension/finite/L2 checks.
            # Avoid revalidating/scaling/norming the query for every Chunk.
            score = max(max(-1., min(1., math.fsum(a*b for a, b in zip(
                query_vector, rows[spec['chunk_id']]['vector'], strict=True)))) for query_vector in query_vectors)
            chunk = chunks[spec['chunk_id']]
            if score <= self.settings.semantic_min_score:
                continue
            if evidence_candidates is not None:
                evidence_candidates.append(EvidenceCandidate(snapshot[0]['id'], root, chunk, score))
                evidence_candidates.sort(key=lambda row: (-row.dense_score, row.chunk['id']))
                del evidence_candidates[self.settings.search_evidence_top_n:]
            previous = best.get(root)
            if previous is None or (-score, chunk['id']) < (-previous[0], previous[1]['id']):
                best[root] = (score, chunk)
        return best

    def rerank(self, query, semantic, by_bag, deadline):
        versions = {key: getattr(by_bag[key][0], 'resource_version_id', None) or key for key in semantic}
        keys = diverse_candidates(semantic, versions, self.settings.semantic_rerank_top_k)
        if not keys:
            return semantic
        deadline()
        scores = self.embedding.rerank(query, [by_bag[key][1]['text'] for key in keys])
        deadline()
        if len(scores) != len(keys):
            raise IndexingError('INVALID_EMBEDDING', 503)
        # Rerank only the bounded dense candidates; no incomparable score mixing.
        return dict(zip(keys, scores, strict=True))

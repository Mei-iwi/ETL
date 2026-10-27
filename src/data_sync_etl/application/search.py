"""Search reads verified Index snapshots; it never queues processing or writes data."""

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from threading import BoundedSemaphore
from time import monotonic

from data_sync_etl.application.content_bags import bag_structure, representation_mode
from data_sync_etl.application.http_query_cache import HttpQueryVectorCache
from data_sync_etl.application.search_runtime import (
    CURRENT_SEARCH,
    SearchCall,
    SearchInferenceQueue,
)
from data_sync_etl.domain.concepts import (
    concept_analysis_scope,
    empty_dictionary,
    equivalent_terms,
)
from data_sync_etl.domain.context_ranking import CONTEXT_RANKING_VERSION, ContextRanker
from data_sync_etl.domain.evidence_selection import select_evidence
from data_sync_etl.domain.indexing import IndexingError, digest, validate_vector, word_windows
from data_sync_etl.domain.lexical import (
    ANALYZER_VERSION,
    RANKING_VERSION,
    REPRESENTATION_VERSION,
    analysis_scope,
    bm25,
    query_terms,
    snippet_text,
)
from data_sync_etl.domain.meaning_bags import match_meaning
from data_sync_etl.domain.query_variants import VERSION as ACCENT_VARIANT_VERSION
from data_sync_etl.domain.query_variants import approved_accent_variant
from data_sync_etl.domain.retrieval_fusion import version_rrf
from data_sync_etl.domain.search import SearchError, cosine, normalize_query, rrf
from data_sync_etl.domain.search_access import SearchAccess, denied
from data_sync_etl.domain.search_context import decode_context, encode_context
from data_sync_etl.domain.vector_admission import VERSION as VECTOR_ADMISSION_VERSION
from data_sync_etl.domain.vector_admission import anchored_dense_match
from data_sync_etl.ports.metadata_search import query_encoding_scope
from data_sync_etl.ports.search import SearchBagReader, document_validation_scope

_REQUEST_DICTIONARY = ContextVar('request_concept_dictionary', default=None)


class QueryEmbeddingContext:
    """One vector for one evaluation query; never cache retrieval or permissions.

    Owned by the sequential evaluation loop, not shared across threads/requests.
    Only a digest is retained as the key. prepare() still runs on every method,
    including hits, so a missing/invalid model cannot be hidden by vector reuse.
    """

    def __init__(self):
        self.clear()

    def clear(self):
        self._owner = self._key = self._vector = None

    def encode(self, embedding, encoder, query, dimension):
        key = digest([query, embedding.model_id, embedding.version, embedding.dimension, dimension])
        if self._owner is embedding and self._key == key:
            return validate_vector(list(self._vector), dimension)
        self.clear()
        vector = validate_vector(encoder(query), dimension)
        self._owner, self._key, self._vector = embedding, key, tuple(vector)
        return list(vector)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.clear()


class SearchService:
    def __init__(self, indexing, settings, bags: SearchBagReader | None, *, query_cache_runtime=None,
                 semantic=None, metadata=None, concept_resolver=None):
        self.indexing, self.settings, self.bags = indexing, settings, bags
        self.semantic = semantic
        self.metadata = metadata
        self.dictionary = empty_dictionary()
        self.concept_resolver = concept_resolver

        self._http_inference = SearchInferenceQueue()
        # Retain worker leases after HTTP timeout: repeated cancellations cannot
        # spawn unbounded abandoned model/driver threads. BoW has separate room.
        self._http_heavy_calls = BoundedSemaphore(16)
        self._http_light_calls = BoundedSemaphore(8)
        self._http_query_vectors = (HttpQueryVectorCache(query_cache_runtime)
                                    if query_cache_runtime is not None else None)

    @property
    def dictionary(self):
        current = _REQUEST_DICTIONARY.get()
        return current[1] if current and current[0] is self else self._base_dictionary

    @dictionary.setter
    def dictionary(self, value):
        self._base_dictionary = value

    def _ranking_algorithm(self, method):
        base = self._base_ranking_algorithm(method)
        return base + '+approved-accent-max-v1' if self.settings.search_approved_accent_variants_enabled else base

    def _base_ranking_algorithm(self, method):
        if method != 'bow' and self.settings.search_evidence_strategy != 'current':
            return 'bge-m3-version-' + self.settings.search_evidence_strategy + '-v1'
        if method == 'hybrid' and self.settings.search_hybrid_fusion_unit == 'version':
            if self.settings.search_hybrid_lexical_weight != 1. or self.settings.search_hybrid_vector_weight != 1.:
                return 'resource-version-weighted-rrf-v1'
            return 'resource-version-rrf-v1'
        if self.settings.semantic_enabled and method in ('vector', 'hybrid'):
            return ('bge-m3-chunk-' + self.settings.semantic_strategy +
                    ('-colbert-v1' if self.settings.semantic_rerank_enabled else '-cosine-v1') +
                    ('+rrf-v1' if method == 'hybrid' else ''))
        meanings = self.settings.index_meaning_bags_enabled
        if meanings:
            return {'bow': RANKING_VERSION, 'vector': 'meaning-context-cosine-v4',
                    'hybrid': 'rrf-meaning-context-v4'}[method]
        return {'bow': RANKING_VERSION,
                'vector': 'meaning-maxcosine-v2' if meanings else 'bag-cosine-threshold-v3',
                'hybrid': 'rrf-meaning-v2' if meanings else 'rrf-vector-threshold-v3'}[method]

    def search_http(self, query, method='hybrid', filters=None, page=1, size=10, *, access=None):
        """Called only inside the route's existing authorization/admission guard.

        Direct Search/Evaluation callers keep the original uncached search().
        Runtime prepare/schema and all live retrieval remain in that function.
        """
        cache = self._http_query_vectors
        if CURRENT_SEARCH.get() is None:
            with self.runtime_admission(method, access=access or SearchAccess()):
                return self.search_http(query, method, filters, page, size, access=access)
        CURRENT_SEARCH.get().check()
        if (method != 'bow' and access is not None and access.internal and not self.settings.semantic_enabled
                and not self.settings.search_metadata_enabled):
            ready = getattr(self.indexing.embedding, 'require_search_ready', None)
            if ready is not None:
                ready()
        # HTTP Search alone opts in; direct/shared callers keep point reads.
        # Initial and final snapshots always hydrate independently.
        criteria = dict(query=query, method=method, filters=filters, page=page, size=size, access=access,
                        _batch_texts=True, _batch_bags=True)
        if self.settings.search_metadata_enabled:
            return self.search(**criteria)
        if self.settings.semantic_enabled and method != 'bow':
            # Legacy query cache is model-specific; never reuse its bag query vectors.
            return self.search(**criteria)
        if not self.settings.search_query_embedding_cache_enabled or method == 'bow' or cache is None:
            if cache is not None and not self.settings.search_query_embedding_cache_enabled:
                cache.clear()
                cache.count('bypass_disabled')
            return self.search(**criteria)
        if (type(access) is not SearchAccess or not access.internal
                or self.settings.app_environment != 'development' or not self.settings.admin_enabled
                or not self.settings.search_internal_dev_enabled):
            cache.clear()
            cache.count('bypass_scope')
            return self.search(**criteria)
        with cache.request() as context:
            result = self.search(**criteria, _query_context=context)
            context.commit()
            return result

    @contextmanager
    def runtime_admission(self, method, *, access, call=None):
        """Own a bounded HTTP worker, NOT the whole pipeline's inference lock."""
        call = call or self.new_http_call(method, access=access)
        token = None
        try:
            if not call.claim():
                raise SearchError('SEARCH_TIMEOUT')
            token = CURRENT_SEARCH.set(call)
            call.check()
            yield call
        finally:
            if token is not None:
                CURRENT_SEARCH.reset(token)
            call.finish()

    def new_http_call(self, method, *, access, authorize=None):
        capacity = (self._http_heavy_calls if method != 'bow' and access.internal
                    else self._http_light_calls)
        if not capacity.acquire(blocking=False):
            raise SearchError('SEARCH_RUNTIME_BUSY', 503)
        return SearchCall(self.settings.search_timeout_seconds, capacity.release,
                          self._http_inference.wake, authorize=authorize)

    @contextmanager
    def _inference(self, *, metadata=False):
        call = CURRENT_SEARCH.get()
        if call is None:  # Direct Search/Evaluation keeps its existing lifecycle.
            yield
            return
        with self._http_inference.slot(call):
            call.reauthorize()
            ready = getattr(self.indexing.embedding, 'require_search_ready', None)
            if ready is not None and not self.settings.semantic_enabled and not metadata:
                ready()
            yield

    def _deadline(self, started):
        call = CURRENT_SEARCH.get()
        if call is not None:
            call.check()
        if monotonic() - started > self.settings.search_timeout_seconds:
            raise SearchError('SEARCH_TIMEOUT')

    def _query_vector(self, query, context, started):
        if CURRENT_SEARCH.get() is not None:
            ready = getattr(self.indexing.embedding, 'require_search_ready', None)
            if ready is not None:
                ready()
        # Only this phase is FIFO/serialized. No request access data is shared.
        with self._inference():
            warmup_started = monotonic()
            embedding = self.indexing.embedding
            prepare_query = getattr(embedding, 'prepare_query', None)
            if prepare_query is None:
                embedding.prepare()
                encoder = embedding.embed
            else:
                encoder = prepare_query()
            warmup_seconds = monotonic() - warmup_started
            if warmup_seconds > self.settings.search_model_warmup_timeout_seconds:
                raise SearchError('SEARCH_MODEL_WARMUP_TIMEOUT')
            # Legacy direct callers retain their separate warmup allowance.
            # HTTP has an additional absolute 15s budget, including queue/prepare.
            started += warmup_seconds
            self._deadline(started)
            dimension = self.settings.index_embedding_dimension
            vector = (context.encode(embedding, encoder, query, dimension) if context is not None
                      else validate_vector(encoder(query), dimension))
            self._deadline(started)
            return vector, started

    def _ready_snapshot(self, repo, version_id, *, batch_texts=False, expected_bags=None,
                        batch_bags=False, bag_reader=None):
        runs = self.indexing._runs(repo, version_id)
        if not runs or runs[0]['status'] != 'COMPLETED':
            return None
        try:
            snapshot = (self.indexing._snapshot(repo, version_id, text_reader=repo.search_unit_texts)
                        if batch_texts else self.indexing._snapshot(repo, version_id))
            if runs[0]['input_fingerprint'] != snapshot[3]:
                return None
            options = {} if expected_bags is None else {'expected_bags': expected_bags}
            reader = bag_reader or (getattr(self.bags, 'search_bags', None) if batch_bags else None)
            if reader is not None:
                options['bag_reader'] = reader
            self.indexing._verified(snapshot, runs[0], **options)
            return snapshot
        except IndexingError:
            return None

    def _postgres_version_changed(self, repo, snapshot):
        """Proof of a version-local PG change, NOT a Mongo freshness marker.

        Called only on the T1 failure/recovery path. Discard ALL artifacts for
        this version; remaining versions still undergo independent live checks.
        Unknown/index/Mongo-only failures must retain SEARCH_INDEX_STALE.
        """
        old = snapshot[0]
        version = repo.get('resource_versions', old['id'])
        resource = repo.get('master_learning_resources', old['resource_id'])
        if (not version or not resource or denied(resource) or denied(version)
                or resource['publication_status'] != 'PUBLISHED'
                or version['resource_id'] != old['resource_id']
                or version['source_hash'] != old['source_hash']
                or version['lifecycle_status'] not in ('INGESTED', 'APPROVED', 'PUBLISHED')):
            return True
        units = repo.find('content_units', {'resource_version_id': old['id']},
                          order=('sequence_no', 'id'), limit=self.settings.index_max_units + 1)
        if len(units) > min(self.settings.index_max_units, 10000):
            raise SearchError('SEARCH_INDEX_STALE', 409)
        previous = {unit['id']: unit for unit in snapshot[2]}
        current = {unit['id']: unit for unit in units}
        if len(current) != len(units):
            raise SearchError('SEARCH_INDEX_STALE', 409)
        if current.keys() != previous.keys():
            return True
        fields = ('parent_unit_id', 'unit_type', 'title', 'sequence_no', 'page_from', 'page_to',
                  'review_status', 'retrieval_eligible', 'mongo_id', 'source_locator')
        if any(any(unit[key] != previous[key_id][key] for key in fields)
               for key_id, unit in current.items()):
            return True
        texts = repo.search_unit_texts(list(current))
        return any(key not in texts or texts[key]['text'] != previous[key]['text'] for key in current)

    def _allowed_units(self, repo, snapshot, access):
        resource = repo.get('master_learning_resources', snapshot[0]['resource_id'])
        nodes = {unit['id']: unit for unit in snapshot[2]}
        allowed = set()
        for unit in snapshot[2]:
            if not access.allows(resource or {}, snapshot[0], unit):
                continue
            ancestor = unit
            while ancestor is not None and access.allows(resource, snapshot[0], ancestor):
                ancestor = nodes.get(ancestor['parent_unit_id'])
            if ancestor is None:
                allowed.add(unit['id'])
        return allowed

    @staticmethod
    def _metadata(repo, filters, access, limit):
        if not access.internal:
            return []  # No existing contract can prove public full-text rights.
        return repo.search_versions({k: v for k, v in filters.items() if k != 'domain'}, limit=limit)

    def _snippets(self, snapshot, keywords, started, allowed, query=None, expansion=None, policy=None):
        roots = bag_structure(snapshot[2])
        # Bag scores include ALL member keywords/vectors. Never expose a Bag that
        # embeds denied descendants, even when another leaf could supply a snippet.
        roots = {root: leaves for root, leaves in roots.items() if set(leaves) <= allowed}
        leaf_root = {leaf: root for root, leaves in roots.items() for leaf in leaves}
        best, chunks = {}, []
        for chunk in self.indexing._preview(snapshot):
            self._deadline(started)
            root = leaf_root.get(chunk['content_unit_id'])
            if root is None or chunk['content_unit_id'] not in allowed or len(chunk['text']) > 20000:
                continue
            chunks.append(chunk)
        ranker = ContextRanker(query, keywords, [chunk['text'] for chunk in chunks],
                               checkpoint=lambda: self._deadline(started),
                               expand_concepts=self.settings.search_expansion.enabled,
                               expansion=expansion, dictionary=self.dictionary, policy=policy)
        for ordinal, chunk in enumerate(chunks):
            self._deadline(started)
            root = leaf_root[chunk['content_unit_id']]
            score = ranker.score(ordinal)
            if root not in best or score > best[root][0]:
                best[root] = (score, chunk)
        return roots, best, chunks

    def _lexical_evidence(self, expansion, bag, snapshot, policy):
        evidence = [{**row, 'content_unit_id': bag.id} for row in
                    self.dictionary.evidence(expansion, bag.title or '', field='title', policy=policy)]
        members = set(bag_structure(snapshot[2])[bag.id])
        for unit in snapshot[2]:
            if unit['id'] in members:
                evidence.extend({**row, 'content_unit_id': unit['id']} for row in
                                self.dictionary.evidence(expansion, unit['text'], policy=policy))
            if len(evidence) >= 64:
                break
        return evidence[:64]

    @analysis_scope()
    @concept_analysis_scope()
    @document_validation_scope()
    @query_encoding_scope()
    def search(self, query, method='hybrid', filters=None, page=1, size=10, *, access=None,
               _query_context=None, _batch_texts=False, _batch_bags=False, _evaluation_capture=None):
        options = dict(access=access, _query_context=_query_context, _batch_texts=_batch_texts,
                       _batch_bags=_batch_bags, _evaluation_capture=_evaluation_capture)
        provider = self.concept_resolver
        call = CURRENT_SEARCH.get()
        if call is not None:
            call.concept_guard = None
        if provider is None or method == 'vector':
            return self._search(query, method, filters, page, size, **options)
        from data_sync_etl.ports.concept_corpus import ConceptBlocked
        try:
            with provider.pin(query, (filters or {}).get('domain')) as dictionary:
                if call is not None:
                    call.concept_guard = getattr(dictionary, 'visibility_guard', None)
                token = _REQUEST_DICTIONARY.set((self, dictionary))
                try:
                    return self._search(query, method, filters, page, size, **options)
                finally:
                    _REQUEST_DICTIONARY.reset(token)
        except ConceptBlocked as error:
            if str(error) != 'DYNAMIC_REVOKED':
                raise
            # Revocation changed during ranking: discard dynamic response and
            # recompute once. Never return its aliases, evidence or fingerprint.
            token = _REQUEST_DICTIONARY.set((self, self._base_dictionary))
            if call is not None:
                call.concept_guard = None
            try:
                return self._search(query, method, filters, page, size, **options)
            finally:
                _REQUEST_DICTIONARY.reset(token)

    def _search(self, query, method='hybrid', filters=None, page=1, size=10, *, access=None,
                _query_context=None, _batch_texts=False, _batch_bags=False, _evaluation_capture=None):
        access = access or SearchAccess()
        options = dict(access=access, _query_context=_query_context, _batch_texts=_batch_texts,
                       _batch_bags=_batch_bags, _evaluation_capture=_evaluation_capture)
        if not self.settings.search_metadata_enabled or not access.internal:
            return self._content_search(query, method, filters, page, size, **options)
        from data_sync_etl.application.metadata_search import fuse_results

        if _evaluation_capture is not None:
            raise SearchError('METADATA_EVALUATION_REQUIRES_SEPARATE_CORPUS', 422)
        if method not in ('bow', 'vector', 'hybrid'):
            raise SearchError('INVALID_SEARCH_METHOD', 422)
        started, query, filters = monotonic(), normalize_query(query), filters or {}
        metadata_items, snapshots, branch_scores = [], {}, {}
        status = {'metadata': 'METADATA_SCHEMA_NOT_READY'}
        if self.metadata is not None:
            try:
                with self._inference(metadata=True) if method != 'bow' else nullcontext():
                    metadata_items, snapshots, metadata_status, branch_scores = self.metadata.retrieve(
                        query, method, filters, access, dictionary=self.dictionary, policy=self.settings.search_expansion,
                        checkpoint=lambda: self._deadline(started))
                status.update({f'metadata_{key}': value for key, value in metadata_status.items()})
                status['metadata'] = 'READY'
            except SearchError as exc:
                if exc.code != 'METADATA_CANDIDATE_LIMIT':
                    raise
                status['metadata'] = exc.code  # Bound metadata without disabling authorized content retrieval.
            except Exception as exc:
                status['metadata'] = exc.code if isinstance(exc, IndexingError) else 'METADATA_UNAVAILABLE'
        self._deadline(started)
        content = dict(query=query, method=method, page=page, size=size, items=[], ready_bag_count=0,
                       representation_unit='content_bag', access_scope=access.scope, message='NO_READY_INDEX')
        content_branches = {}
        if self.settings.search_metadata_content_enabled:
            try:
                content = self._content_search(query, method, filters, 1, self.settings.search_max_bags,
                                                **options, _branch_capture=content_branches)
                status['content'] = content['message']
            except (SearchError, IndexingError) as exc:
                if exc.code not in ('SEARCH_SCHEMA_UNAVAILABLE', 'BLOCKED_INDEX_SCHEMA', 'BLOCKED_EMBEDDING_RUNTIME',
                                    'SEMANTIC_SCHEMA_NOT_READY', 'SEMANTIC_MODEL_UNAVAILABLE',
                                    'SEMANTIC_INFERENCE_FAILED', 'SEMANTIC_MODEL_IDENTITY_CHANGED',
                                    'SEMANTIC_INDEX_STALE', 'SEMANTIC_INDEX_NOT_READY',
                                    'SEMANTIC_INDEX_INCOMPLETE', 'SEARCH_INDEX_STALE') and not (
                        self.settings.search_metadata_indexed_candidates_enabled and exc.code == 'SEARCH_LIMIT_EXCEEDED'):
                    raise
                status['content'] = exc.code
                if method == 'hybrid' and exc.code in ('SEMANTIC_INDEX_STALE', 'SEMANTIC_INDEX_NOT_READY',
                        'SEMANTIC_INDEX_INCOMPLETE', 'SEMANTIC_MODEL_UNAVAILABLE',
                        'SEMANTIC_INFERENCE_FAILED', 'SEMANTIC_MODEL_IDENTITY_CHANGED'):
                    content_branches.clear()
                    content = self._content_search(query, 'bow', filters, 1, self.settings.search_max_bags,
                        **options, _branch_capture=content_branches)
                    content['method'] = method
                    status.update(content='PARTIAL', content_dense=exc.code, content_lexical=content['message'])
        else:
            status['content'] = 'DISABLED'
        # Metadata final validation follows all content inference. A failed live
        # metadata read discards metadata candidates, while content keeps its own
        # independently verified snapshot and ACL contract.
        if metadata_items:
            try:
                metadata_hit_count = len(metadata_items)
                metadata_items = self.metadata.final_validate(metadata_items, snapshots, filters, access,
                    checkpoint=lambda: self._deadline(started))
                if metadata_hit_count and not metadata_items:
                    status['metadata'] = 'STALE'
                if content['items']:
                    content['items'] = self.metadata.final_validate(content['items'], snapshots, filters, access,
                        checkpoint=lambda: self._deadline(started), content=True)
            except SearchError:
                raise
            except Exception:
                metadata_items = []
                status['metadata'] = 'METADATA_FINAL_CHECK_UNAVAILABLE'
        valid = {item['resource_version_id'] for item in metadata_items}
        branches = {name: {key: score for key, score in scores.items() if key in valid}
                    for name, scores in branch_scores.items()}
        branches.update(content_branches)
        rows = fuse_results(content['items'], metadata_items, branches)
        self._deadline(started)
        offset, total = (page - 1) * size, len(rows)
        metadata_ready = bool(snapshots) and status.get('metadata') == 'READY' and (
            (method in ('bow', 'hybrid') and status.get('metadata_lexical') == 'READY')
            or (method in ('vector', 'hybrid') and status.get('metadata_dense') == 'READY'))
        return {**content, 'page': page, 'size': size, 'items': rows[offset:offset + size],
                'total': total, 'total_pages': (total + size - 1) // size, 'branch_status': status,
                'ranking_algorithm': 'metadata-content-four-source-version-rrf-opt-in-v1',
                'message': 'OK' if rows or metadata_ready else content['message']}

    def _content_search(self, query, method='hybrid', filters=None, page=1, size=10, *, access=None,
                        _query_context=None, _batch_texts=False, _batch_bags=False, _evaluation_capture=None,
                        _branch_capture=None):
        access = access or SearchAccess()
        started, filters = monotonic(), filters or {}
        query = normalize_query(query)
        if not access.internal:
            return {'query': query, 'method': method, 'page': page, 'size': size,
                    'total': 0, 'total_pages': 0, 'items': [], 'ready_bag_count': 0,
                    'representation_unit': 'content_bag', 'access_scope': 'public',
                    'ranking_algorithm': self._ranking_algorithm(method),
                    'message': 'PUBLIC_ACCESS_UNCONFIRMED'}
        keywords = query_terms(query)
        policy = self.settings.search_expansion.model_copy(deep=True)
        expansion = self.dictionary.expand(query, domain=filters.get('domain'), policy=policy)
        variant = (approved_accent_variant(query, self.dictionary, policy=policy, domain=filters.get('domain'))
                   if method != 'vector' and self.settings.search_approved_accent_variants_enabled else None)
        variant_expansion = self.dictionary.expand(variant, domain=filters.get('domain'), policy=policy) if variant else None
        variant_keywords = query_terms(variant) if variant else []
        extra_vectors = []
        if self.bags is None:
            raise SearchError('SEARCH_SCHEMA_UNAVAILABLE')
        self.bags.ensure_ready()
        self._deadline(started)
        vector = None
        use_semantic = self.settings.semantic_enabled and method in ('vector', 'hybrid')
        semantic_budget = [0]
        if method in ('vector', 'hybrid'):
            if use_semantic:
                if self.semantic is None:
                    raise SearchError('SEMANTIC_SCHEMA_NOT_READY', 503)
                self.semantic.embedding.require_search_ready()
                with self._inference():
                    vector = self.semantic.query(query)
                self._deadline(started)
            else:
                vector, started = self._query_vector(query, _query_context, started)
        with self.indexing.uow() as repo:
            metadata = self._metadata(repo, filters, access, self.settings.search_max_bags + 1)
        if len(metadata) > self.settings.search_max_bags:
            raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
        by_version = {row['version_id']: row for row in metadata}
        candidates = self.bags.search_candidates(list(by_version),
            model=f'{self.indexing.embedding.model_id}@{self.indexing.embedding.version}',
            limit=self.settings.search_max_bags + 1) if metadata else []
        if len(candidates) > self.settings.search_max_bags:
            raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
        phase_reader = getattr(self.bags, 'search_phase', None) if _batch_bags else None
        initial_phase = (phase_reader(sorted({bag.resource_version_id for bag in candidates}),
                                     limit=self.settings.search_max_bags + 1,
                                     max_chars=self.settings.index_max_source_chars)
                         if phase_reader is not None and candidates else None)
        by_bag, bow, semantic, snapshots, ready_count, source_chars = {}, {}, {}, {}, 0, 0
        excluded, semantic_metadata = set(), {}
        authorized_roots, evidence_by_version = {}, {}
        for version_id in sorted({bag.resource_version_id for bag in candidates}):
            self._deadline(started)
            with self.indexing.uow() as repo:
                snapshot = self._ready_snapshot(repo, version_id, batch_texts=_batch_texts,
                    batch_bags=_batch_bags, bag_reader=initial_phase)
                allowed = self._allowed_units(repo, snapshot, access) if snapshot else set()
                if snapshot and use_semantic:
                    semantic_metadata[version_id] = repo.semantic_metadata(snapshot[0]['resource_id'])
            if snapshot is None:
                continue
            source_chars += sum(len(unit['text']) for unit in snapshot[2])
            if source_chars > self.settings.index_max_source_chars:
                raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
            snapshots[version_id] = snapshot
            roots, snippets, preview = self._snippets(snapshot, keywords, started, allowed, query, expansion, policy)
            if variant is not None:
                _, alternatives, _ = self._snippets(snapshot, variant_keywords, started, allowed,
                                                     variant, variant_expansion, policy)
                for key, candidate in alternatives.items():
                    if candidate[0] > snippets[key][0]:
                        snippets[key] = (candidate[0], {**candidate[1], '_retrieval_query': variant})
            authorized_roots[version_id] = set(roots)
            evidence_options = {}
            if extra_vectors:
                evidence_options['query_vectors'] = extra_vectors
            if use_semantic and self.settings.search_evidence_strategy != 'current':
                evidence_by_version[version_id] = []
                evidence_options['evidence_candidates'] = evidence_by_version[version_id]
            semantic_hits = (self.semantic.scores(snapshot, semantic_metadata[version_id],
                roots, allowed, vector, lambda: self._deadline(started), semantic_budget, **evidence_options)
                if use_semantic else {})
            version_bags = [bag for bag in candidates if bag.resource_version_id == version_id]
            reader = initial_phase or (getattr(self.bags, 'search_bags', None) if _batch_bags else None)
            current_bags = (reader([bag.id for bag in version_bags], version_id=version_id, generation=snapshot[1])
                            if reader is not None else None)
            for bag in version_bags:
                self._deadline(started)
                # Candidate may have been fetched before a concurrent invalidation.
                current = current_bags.get(bag.id) if current_bags is not None else self.bags.get_bag(bag.id)
                if not current or current != bag:
                    if _batch_texts:
                        with self.indexing.uow() as repo:
                            isolated = self._postgres_version_changed(repo, snapshot)
                        if isolated:
                            excluded.add(version_id)
                            break
                    raise SearchError('SEARCH_INDEX_STALE', 409)
                if bag.id not in roots or bag.id not in snippets:
                    continue
                ready_count += 1
                matched = sorted(set(keywords) & set(bag.lexical.terms))
                if variant is not None:
                    matched = sorted(set(matched) | (set(variant_keywords) & set(bag.lexical.terms)))
                if policy.enabled:
                    matched = sorted(set(matched) | equivalent_terms(query, bag.lexical.title_tokens, expansion=expansion, dictionary=self.dictionary, policy=policy)
                                 | set().union(*(equivalent_terms(query, passage, expansion=expansion, dictionary=self.dictionary, policy=policy)
                                                 for passage in bag.lexical.passages)))
                chunk = dict(snippets[bag.id][1])
                chunk['context_evidence'] = 'lexical' if any(snippets[bag.id][0]) else 'bag'
                chunk['meaning_variants'] = []
                if use_semantic:
                    hit = semantic_hits.get(bag.id)
                    if hit is not None:
                        semantic[bag.id] = hit[0]
                        chunk = {**hit[1], 'context_evidence': 'semantic', 'meaning_variants': []}
                elif vector is not None:
                    member, variants = None, []
                    winning_vector = vector
                    if self.settings.index_meaning_bags_enabled:
                        value, member, variants = match_meaning(vector, bag.meanings, context={
                            'query': query, 'keywords': keywords, 'expansion': expansion,
                            'dictionary': self.dictionary, 'policy': policy,
                            'margin': self.settings.search_meaning_context_margin})
                        for extra in extra_vectors:
                            candidate = match_meaning(extra, bag.meanings, context={
                                'query': variant, 'keywords': variant_keywords,
                                'expansion': variant_expansion,
                                'dictionary': self.dictionary, 'policy': policy,
                                'margin': self.settings.search_meaning_context_margin})
                            if candidate[0] > value:
                                value, member, variants = candidate
                                winning_vector = extra
                    else:
                        value = max(cosine(v, bag.embedding) for v in [vector, *extra_vectors])
                    accepted = value >= self.settings.search_vector_min_similarity
                    if not accepted and self.settings.search_vector_anchor_fallback_enabled:
                        # Dense score remains mandatory. Accent-insensitive phrase
                        # evidence is from the selected, freshly authorized source
                        # passage, never a title, private sibling or invented alias.
                        source_text = member.text if member is not None and member.kind == 'text' else (
                            chunk['text'] if member is None else '')
                        accepted = anchored_dense_match(query, source_text, value,
                            floor=self.settings.search_vector_anchor_min_similarity,
                            dictionary=self.dictionary, policy=policy, expansion=expansion)
                    if (not accepted and self.settings.index_meaning_bags_enabled
                            and self.settings.search_meaning_context_fallback_enabled
                            and value >= self.settings.search_meaning_fallback_min_similarity):
                        # A weak short expression alone is not enough. Require
                        # independent same-bag topic support, without lexical
                        # overlap, translation services or another model call.
                        accepted = cosine(winning_vector, bag.embedding) >= self.settings.search_meaning_fallback_min_context_similarity
                    if accepted:
                        semantic[bag.id] = value
                        chunk['meaning_variants'] = variants[:8]
                        if member is not None and member.kind == 'text' and (
                                method == 'vector' or not snippets[bag.id][0][0]):
                            passages = [part for part in preview if part['content_unit_id'] == member.unit_id
                                          and part['char_start'] < member.end and part['char_end'] > member.start]
                            if passages:
                                chosen = max(passages, key=lambda part: (
                                    min(part['char_end'], member.end) - max(part['char_start'], member.start),
                                    -part['unit_chunk_sequence']))
                                chunk = {**chosen, 'context_evidence': 'semantic',
                                         'meaning_variants': variants[:8],
                                         'anchor': max(member.start - chosen['char_start'], 0)}
                by_bag[bag.id] = (bag, chunk, matched,
                    representation_mode(snapshot[2], {bag.id: roots[bag.id]}))

        if use_semantic and self.settings.semantic_rerank_enabled:
            with self._inference():
                semantic = self.semantic.rerank(query, semantic, by_bag, lambda: self._deadline(started))

        def discard_versions():
            nonlocal by_bag, bow, semantic, snapshots, ready_count
            by_bag = {key: value for key, value in by_bag.items()
                      if value[0].resource_version_id not in excluded}
            bow = {key: value for key, value in bow.items() if key in by_bag}
            semantic = {key: value for key, value in semantic.items() if key in by_bag}
            snapshots = {key: value for key, value in snapshots.items() if key not in excluded}
            ready_count = len(by_bag)
        if excluded:
            discard_versions()
        def ranked():
            nonlocal bow
            bow = bm25(query, {key: row[0].lexical for key, row in by_bag.items()},
                       dictionary=self.dictionary, policy=policy, expansion=expansion,
                       min_coverage=self.settings.search_bow_min_coverage, focus_query=variant)
            if variant is not None:
                assisted = bm25(variant, {key: row[0].lexical for key, row in by_bag.items()},
                    dictionary=self.dictionary, policy=policy, domain=filters.get('domain'),
                    min_coverage=self.settings.search_bow_min_coverage)
                # Union by identity and maximum score, never duplicate-score addition.
                bow = {key: max(bow.get(key, 0), policy.synonyms * assisted.get(key, 0))
                       for key in bow.keys() | assisted.keys()}
            scores = bow if method == 'bow' else semantic if method == 'vector' else rrf(bow, semantic)
            fusion = (version_rrf({'bow': bow, 'vector': semantic},
                                 {key: row[0].resource_version_id for key, row in by_bag.items()},
                                 weights=({'bow': self.settings.search_hybrid_lexical_weight,
                                           'vector': self.settings.search_hybrid_vector_weight}
                                          if self.settings.search_hybrid_lexical_weight != 1.
                                          or self.settings.search_hybrid_vector_weight != 1. else None))
                      if method == 'hybrid' and self.settings.search_hybrid_fusion_unit == 'version' else None)
            if fusion is not None:
                scores = fusion.scores
            grouped, matches = {}, {}
            for bag_id in sorted(scores, key=lambda key: (-scores[key],
                    bool(fusion and key != fusion.representatives[by_bag[key][0].resource_version_id]), key)):
                bag, chunk, matched, mode = by_bag[bag_id]
                row = by_version[bag.resource_version_id]
                matches[row['resource_id']] = matches.get(row['resource_id'], 0) + 1
                if row['resource_id'] in grouped:
                    continue
                # A reversible, snapshot-bound identifier is added by the context helper.
                snapshot = snapshots[bag.resource_version_id]
                token = encode_context(bag.resource_version_id, chunk['content_unit_id'],
                                       chunk['unit_chunk_sequence'], snapshot[3])
                grouped[row['resource_id']] = {
                    **{key: value for key, value in row.items() if key != 'version_id'},
                    'resource_version_id': bag.resource_version_id,
                    'bag_id': bag.id, 'bag_title': bag.title, 'unit_type': bag.unit_type,
                    'representation_mode': mode, 'matched_keywords': matched, 'score': scores[bag_id],
                    'score_components': (fusion.components[bag.resource_version_id] if fusion else
                                         {key: branch[bag_id] for key, branch in
                                          [('bow', bow), ('vector', semantic)] if bag_id in branch}),
                    'page_from': chunk['page_from'], 'page_to': chunk['page_to'],
                    'content_unit_id': chunk['content_unit_id'], 'context_id': token,
                    'context_evidence': chunk['context_evidence'],
                    'meaning_variants': chunk['meaning_variants'],
                    'context_url': f'/api/v1/chunks/{token}/context',
                }
            # Rendering does not affect rank/total. Hydrate only the visible
            # winners, BEFORE final snapshot/ACL validation, including on rerank.
            visible = sorted(grouped.values(), key=lambda row: (-row['score'], row['resource_id']))
            offset = (page - 1) * size
            for item in visible[offset:offset + size]:
                self._deadline(started)
                bag, chunk, *_ = by_bag[item['bag_id']]
                version_id = bag.resource_version_id
                snapshot = snapshots[version_id]
                if use_semantic and self.settings.search_evidence_strategy != 'current' and method != 'bow':
                    selected = select_evidence(evidence_by_version.get(version_id, []), version_id=version_id,
                        strategy='dense' if self.settings.search_evidence_strategy == 'version_dense' else 'context',
                        top_n=self.settings.search_evidence_top_n, margin=self.settings.search_evidence_dense_margin,
                        query=query, keywords=keywords, dictionary=self.dictionary, expansion=expansion, policy=policy,
                        checkpoint=lambda: self._deadline(started))
                    if selected is not None:
                        current = by_bag.get(selected.bag_id)
                        if (current is None or current[0].resource_version_id != version_id
                                or selected.chunk['content_unit_id'] not in
                                bag_structure(snapshots[version_id][2]).get(selected.bag_id, ())):
                            raise SearchError('SEARCH_INDEX_STALE', 409)
                        bag, _, matched, mode = by_bag[selected.bag_id]
                        chunk = {**selected.chunk, 'context_evidence': 'semantic', 'meaning_variants': []}
                        token = encode_context(version_id, chunk['content_unit_id'],
                                               chunk['unit_chunk_sequence'], snapshots[version_id][3])
                        item.update(bag_id=bag.id, bag_title=bag.title, unit_type=bag.unit_type,
                            representation_mode=mode, matched_keywords=matched, content_unit_id=chunk['content_unit_id'],
                            page_from=chunk['page_from'], page_to=chunk['page_to'], context_id=token,
                            context_url=f'/api/v1/chunks/{token}/context', context_evidence='semantic', meaning_variants=[])
                        if method == 'vector':
                            item['score_components'] = {'vector': item['score']}
                rendered_variant = variant is not None and chunk.get('_retrieval_query') == variant
                rendered_expansion = variant_expansion if rendered_variant else expansion
                item['snippet'] = snippet_text(chunk['text'], set(variant_keywords if rendered_variant else keywords),
                    query=variant if rendered_variant else query,
                    anchor=chunk.get('anchor'), expansion=rendered_expansion, dictionary=self.dictionary, policy=policy)
                locator = next((u.get('source_locator') for u in snapshot[2] if u['id'] == chunk['content_unit_id']), None)
                if locator:
                    item['source_locator'] = locator
                else:
                    item.pop('source_locator', None)
                item['lexical_evidence'] = self._lexical_evidence(rendered_expansion, bag, snapshots[bag.resource_version_id], policy)
            return grouped, matches
        grouped, matches = ranked()
        whole_file_access = {}
        # Recheck metadata and snapshots after ranking; never publish a known stale vector.
        if snapshots or (_batch_texts and excluded):
            with self.indexing.uow() as repo:
                fresh_rows = self._metadata(repo, filters, access, self.settings.search_max_bags + 1)
                fresh_by_version = {row['version_id']: row for row in fresh_rows}
                fresh_ids = set(fresh_by_version)
                if (len(fresh_rows) > self.settings.search_max_bags
                        or fresh_ids - by_version.keys()):
                    raise SearchError('SEARCH_INDEX_STALE', 409)
                checked_versions = set(snapshots)  # Nonhits also influence corpus statistics.
                expected_bags = {bag.id: bag for bag in candidates}
                final_phase = (phase_reader(sorted(checked_versions), limit=self.settings.search_max_bags + 1,
                                            max_chars=self.settings.index_max_source_chars)
                               if phase_reader is not None and checked_versions else None)
                for version_id in checked_versions:
                    fresh = self._ready_snapshot(repo, version_id, batch_texts=_batch_texts,
                                                 expected_bags=expected_bags, batch_bags=_batch_bags,
                                                 bag_reader=final_phase)
                    if version_id not in fresh_ids or fresh is None or fresh[3] != snapshots[version_id][3]:
                        if _batch_texts and (version_id not in fresh_ids or
                                self._postgres_version_changed(repo, snapshots[version_id])):
                            excluded.add(version_id)
                            continue
                        raise SearchError('SEARCH_INDEX_STALE', 409)
                    if _batch_texts and fresh_by_version[version_id] != by_version[version_id]:
                        raise SearchError('SEARCH_INDEX_STALE', 409)
                    if use_semantic and authorized_roots[version_id]:
                        if fresh_by_version[version_id] != by_version[version_id]:
                            raise SearchError('SEARCH_INDEX_STALE', 409)
                        fresh_metadata = repo.semantic_metadata(fresh[0]['resource_id'])
                        if fresh_metadata != semantic_metadata[version_id]:
                            raise SearchError('SEMANTIC_INDEX_STALE', 409)
                        plan, _ = self.semantic.plan(fresh, fresh_metadata)
                        manifest = self.semantic.store.manifest(plan['_id'])
                        if not manifest or manifest['status'] != 'READY':
                            raise SearchError('SEMANTIC_INDEX_STALE', 409)
                    allowed = self._allowed_units(repo, fresh, access)
                    whole_file_access[version_id] = bool(fresh[2]) and {unit['id'] for unit in fresh[2]} <= allowed
                    fresh_roots = {root for root, leaves in bag_structure(fresh[2]).items()
                                   if set(leaves) <= allowed}
                    if fresh_roots != authorized_roots[version_id]:
                        raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
                    if any(not set(bag_structure(fresh[2])[bag_id]) <= allowed
                           for bag_id, (bag, *_) in by_bag.items() if bag.resource_version_id == version_id):
                        if _batch_texts and self._postgres_version_changed(repo, snapshots[version_id]):
                            excluded.add(version_id)
                            continue
                        raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
            if excluded:
                discard_versions()
                grouped, matches = ranked()
        self._deadline(started)
        if _evaluation_capture is not None:
            # Internal evaluation adapter only, never bound from HTTP input.
            # Export ALL admitted Bags (including nonhits) AFTER both phases.
            # Partial index coverage must not silently change corpus statistics.
            if excluded or len(snapshots) != len(by_version):
                raise SearchError('EVALUATION_SNAPSHOT_INCOMPLETE', 409)
            _evaluation_capture(by_bag, snapshots, policy, by_version)
        items = sorted(grouped.values(), key=lambda item: (-item['score'], item['resource_id']))
        for item in items:
            item['matched_bag_count'] = matches[item['resource_id']]
            item['can_view_file'] = whole_file_access.get(item['resource_version_id'], False)
        total, offset = len(items), (page - 1) * size
        if _branch_capture is not None:
            for name, scores in [('content_lexical', bow), ('content_dense', semantic)]:
                if (name == 'content_lexical' and method == 'vector') or (name == 'content_dense' and method == 'bow'):
                    continue
                versions = {}
                for key, score in scores.items():
                    if key in by_bag:
                        version_id = by_bag[key][0].resource_version_id
                        versions[version_id] = max(score, versions.get(version_id, float('-inf')))
                _branch_capture[name] = versions
        vector_info = {}
        if use_semantic:
            selected_versions = {item['resource_version_id'] for item in items[offset:offset+size]}
            vector_info['vector_index'] = {
                **self.semantic.embedding.model_identity(), 'strategy': self.settings.semantic_strategy,
                'rerank_enabled': self.settings.semantic_rerank_enabled,
                'index_ids': sorted(self.semantic.plan(snapshots[key], semantic_metadata[key])[0]['_id']
                                    for key in selected_versions)}
        return {'query': query, 'method': method, 'page': page, 'size': size, 'total': total,
                **vector_info,
                'total_pages': (total + size - 1) // size, 'items': items[offset:offset+size],
                'ranking_algorithm': self._ranking_algorithm(method),
                'query_expansion': {**expansion.model_dump(), **({'variant_policy': ACCENT_VARIANT_VERSION,
                    'retrieval_queries': [query, *([variant] if variant is not None else [])],
                    'fusion': 'per-bag-max-before-rrf', 'lexical_variant_weight': policy.synonyms,
                    'dictionary_approved_only': True}
                    if self.settings.search_approved_accent_variants_enabled else {})},
                'search_fingerprint': digest([ANALYZER_VERSION, REPRESENTATION_VERSION, RANKING_VERSION,
                                              CONTEXT_RANKING_VERSION,
                                              self.settings.search_bow_min_coverage,
                                              self.settings.search_hybrid_fusion_unit,
                                              self.settings.search_hybrid_lexical_weight,
                                              self.settings.search_hybrid_vector_weight,
                                              self.settings.search_evidence_strategy,
                                              self.settings.search_evidence_top_n,
                                              self.settings.search_evidence_dense_margin,
                                              expansion.dictionary_fingerprint,
                                              expansion.policy_fingerprint,
                                              self.settings.search_vector_min_similarity,
                                              VECTOR_ADMISSION_VERSION,
                                              self.settings.search_vector_anchor_fallback_enabled,
                                              self.settings.search_vector_anchor_min_similarity,
                                              self.settings.index_meaning_bags_enabled,
                                              self.settings.search_meaning_context_fallback_enabled,
                                              self.settings.search_meaning_fallback_min_similarity,
                                              self.settings.search_meaning_fallback_min_context_similarity,
                                              self.settings.search_meaning_context_margin] +
                                              ([ACCENT_VARIANT_VERSION] if self.settings.search_approved_accent_variants_enabled else [])),
                'representation_unit': 'content_bag', 'ready_bag_count': ready_count, 'access_scope': access.scope,
                'readiness': {'lexical_ready_bags': ready_count, 'dense_ready_bags': ready_count,
                              'eligible_versions': len(by_version), 'ready_versions': len(snapshots),
                              'reindex_required': len(snapshots) < len(by_version)},
                'message': 'QUERY_NO_KEYWORDS' if not keywords and method == 'bow' else
                           'NO_READY_INDEX' if metadata and not snapshots else
                           'NO_ACCESSIBLE_CONTENT' if not ready_count else 'OK'}

    def authorize_content_version(self, version_id, *, access=None):
        """Same whole-content rights as File/Context, without requiring an index.

        Local processing/index previews must remain usable before INDEX_BUILD.
        This does not weaken the existing file-viewer INDEX READY gate.
        """
        access = access or SearchAccess()
        if not access.internal:
            raise SearchError('FILE_NOT_FOUND', 404)
        with self.indexing.uow() as repo:
            try:
                version, state = self.indexing._version(repo, version_id)
                resource = repo.get('master_learning_resources', version['resource_id'])
                if (not resource or resource['publication_status'] != 'PUBLISHED'
                        or not resource['is_active'] or denied(resource) or denied(version)):
                    raise SearchError('FILE_NOT_FOUND', 404)
                if state != 'CONTENT_READY':
                    raise SearchError('FILE_NOT_READY', 409)
                snapshot = self.indexing._snapshot(repo, version_id)
                if not {u['id'] for u in snapshot[2]} <= self._allowed_units(repo, snapshot, access):
                    raise SearchError('FILE_NOT_FOUND', 404)
            except IndexingError:
                raise SearchError('FILE_NOT_FOUND', 404) from None

    def file_version(self, version_id, *, access=None, include_text=False):
        """A whole file requires permission for every unit, including parents."""
        access = access or SearchAccess()
        if not access.internal:
            raise SearchError('FILE_NOT_FOUND', 404)
        with self.indexing.uow() as repo:
            snapshot = self._ready_snapshot(repo, version_id)
            if snapshot is None or not snapshot[2]:
                # Diagnose readiness only; this path never grants file/text access.
                version = repo.get('resource_versions', version_id)
                resource = repo.get('master_learning_resources', version['resource_id']) if version else None
                unit_limit = min(self.settings.index_max_units, 10000)
                units = repo.find('content_units', {'resource_version_id': version_id},
                                  limit=unit_limit + 1) if version else []
                if (resource and resource['publication_status'] == 'PUBLISHED'
                        and resource['is_active'] is True and not denied(resource)
                        and version['lifecycle_status'] in ('INGESTED', 'APPROVED', 'PUBLISHED')
                        and not denied(version) and len(units) <= unit_limit
                        and all(access.allows(resource, version, unit) for unit in units)):
                    raise SearchError('FILE_NOT_READY', 409)
                raise SearchError('FILE_NOT_FOUND', 404)
            if not {unit['id'] for unit in snapshot[2]} <= self._allowed_units(repo, snapshot, access):
                raise SearchError('FILE_NOT_FOUND', 404)
            result = dict(snapshot[0])  # Storage coordinates never become a JSON response.
            if include_text:
                content = '\n\n'.join(unit['text'] for unit in snapshot[2] if unit['text'])
                result['preview_text'] = content[:200000] + ('\n… Tải tệp để xem toàn bộ.' if len(content) > 200000 else '')
            return result

    def context(self, token, neighbors=1, *, access=None):
        access = access or SearchAccess()
        started = monotonic()
        version_id, unit_id, ordinal, fingerprint = decode_context(token)
        if not access.internal:
            raise SearchError('CONTEXT_NOT_FOUND', 404)
        if self.bags is None:
            raise SearchError('SEARCH_SCHEMA_UNAVAILABLE')
        self.bags.ensure_ready()
        filters = {'resource_version_id': version_id}
        with self.indexing.uow() as repo:
            rows = self._metadata(repo, filters, access, 1)
            if not rows:
                raise SearchError('CONTEXT_NOT_FOUND', 404)
            try:
                snapshot = self.indexing._snapshot(repo, version_id)
            except IndexingError:
                raise SearchError('CONTEXT_NOT_FOUND', 404) from None
            nodes = {unit['id']: unit for unit in snapshot[2]}
            unit = nodes.get(unit_id)
            roots = bag_structure(snapshot[2])
            root = next((key for key, leaves in roots.items() if unit_id in leaves), None)
            allowed = self._allowed_units(repo, snapshot, access)
            if unit is None or root is None or not set(roots[root]) <= allowed:
                raise SearchError('CONTEXT_NOT_FOUND', 404)
            if snapshot[3][:32] != fingerprint:
                raise SearchError('CONTEXT_STALE', 409)
            if self._ready_snapshot(repo, version_id) is None:
                raise SearchError('CONTEXT_NOT_FOUND', 404)
        chunks = []
        for sequence, (start, end, text) in enumerate(word_windows(
                unit['text'], self.settings.chunk_size_words, self.settings.chunk_overlap_words), 1):
            self._deadline(started)
            if sequence > self.settings.index_max_chunks:
                raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
            if ordinal - neighbors <= sequence <= ordinal + neighbors:
                if len(text) > 20000:
                    raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
                chunks.append({'id': digest([version_id, unit_id, sequence, snapshot[3]])[:30],
                    'content_unit_id': unit_id, 'unit_chunk_sequence': sequence,
                    'char_start': start, 'char_end': end, 'page_from': unit['page_from'],
                    'page_to': unit['page_to'], 'text': text, 'selected': sequence == ordinal,
                    **({'source_locator': unit['source_locator']} if unit.get('source_locator') else {})})
            if sequence > ordinal + neighbors:
                break
        if not any(chunk['selected'] for chunk in chunks):
            raise SearchError('CONTEXT_NOT_FOUND', 404)
        path, current = [], unit
        while current is not None:
            path.append({'content_unit_id': current['id'], 'title': current['title'], 'unit_type': current['unit_type']})
            current = nodes.get(current['parent_unit_id'])
        with self.indexing.uow() as repo:
            fresh = self._ready_snapshot(repo, version_id)
            if not self._metadata(repo, filters, access, 1):
                raise SearchError('CONTEXT_NOT_FOUND', 404)
            if fresh is None or fresh[3] != snapshot[3]:
                raise SearchError('CONTEXT_STALE', 409)
            if not set(roots[root]) <= self._allowed_units(repo, fresh, access):
                raise SearchError('CONTEXT_NOT_FOUND', 404)
        self._deadline(started)
        return {**{key: value for key, value in rows[0].items() if key != 'version_id'},
                'resource_version_id': version_id, 'context_id': token, 'content_unit_id': unit_id,
                'bag_id': root, 'heading_path': list(reversed(path)), 'neighbors': neighbors,
                'chunks': chunks, 'access_scope': access.scope}

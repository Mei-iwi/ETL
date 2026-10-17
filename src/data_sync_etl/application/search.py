"""Search reads verified Index snapshots; it never queues processing or writes data."""

from contextlib import contextmanager
from threading import BoundedSemaphore
from time import monotonic

from data_sync_etl.application.content_bags import bag_structure, representation_mode
from data_sync_etl.domain.indexing import IndexingError, digest, validate_vector, word_windows
from data_sync_etl.domain.search import SearchError, binary_score, cosine, normalize_query, rrf
from data_sync_etl.domain.search_access import SearchAccess, denied
from data_sync_etl.domain.search_context import decode_context, encode_context
from data_sync_etl.ports.search import SearchBagReader


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
    def __init__(self, indexing, settings, bags: SearchBagReader | None):
        self.indexing, self.settings, self.bags = indexing, settings, bags
        self._http_model_slot = BoundedSemaphore(1)

    @contextmanager
    def runtime_admission(self, method, *, access):
        """Bound HTTP Vector/Hybrid work per process, without queued model waiters.

        Only the Search route uses this guard. Direct consumers and shared model
        lifecycle are unchanged. A hung inference cannot be safely interrupted;
        retain its slot until it actually returns, rejecting new heavy requests.
        BoW and fail-closed public responses never acquire the model slot.
        """
        if method == 'bow' or access.scope != 'local_demo':
            yield
            return
        if not self._http_model_slot.acquire(blocking=False):
            raise SearchError('SEARCH_RUNTIME_BUSY', 503)
        try:
            yield
        finally:
            self._http_model_slot.release()

    def _deadline(self, started):
        if monotonic() - started > self.settings.search_timeout_seconds:
            raise SearchError('SEARCH_TIMEOUT')

    def _ready_snapshot(self, repo, version_id):
        runs = self.indexing._runs(repo, version_id)
        if not runs or runs[0]['status'] != 'COMPLETED':
            return None
        try:
            snapshot = self.indexing._snapshot(repo, version_id)
            if runs[0]['input_fingerprint'] != snapshot[3]:
                return None
            self.indexing._verified(snapshot, runs[0])
            return snapshot
        except IndexingError:
            return None

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
        if access.scope != 'local_demo':
            return []  # No existing contract can prove public full-text rights.
        return repo.search_versions(filters, limit=limit)

    def _snippets(self, snapshot, keywords, started, allowed):
        roots = bag_structure(snapshot[2])
        # Bag scores include ALL member keywords/vectors. Never expose a Bag that
        # embeds denied descendants, even when another leaf could supply a snippet.
        roots = {root: leaves for root, leaves in roots.items() if set(leaves) <= allowed}
        leaf_root = {leaf: root for root, leaves in roots.items() for leaf in leaves}
        best = {}
        for chunk in self.indexing._preview(snapshot):
            self._deadline(started)
            root = leaf_root.get(chunk['content_unit_id'])
            if root is None or chunk['content_unit_id'] not in allowed or len(chunk['text']) > 20000:
                continue
            score, _ = binary_score(keywords, self.indexing.extractor.extract(chunk['text']))
            if root not in best or score > best[root][0]:
                best[root] = (score, chunk)
        return roots, best

    def search(self, query, method='hybrid', filters=None, page=1, size=10, *, access=None,
               _query_context=None):
        access = access or SearchAccess()
        started, filters = monotonic(), filters or {}
        query = normalize_query(query)
        if access.scope != 'local_demo':
            return {'query': query, 'method': method, 'page': page, 'size': size,
                    'total': 0, 'total_pages': 0, 'items': [], 'ready_bag_count': 0,
                    'representation_unit': 'content_bag', 'access_scope': 'public',
                    'ranking_algorithm': {'bow': 'binary-keyword-cosine-v1',
                                          'vector': 'bag-cosine-v1', 'hybrid': 'rrf-v1'}[method],
                    'message': 'PUBLIC_ACCESS_UNCONFIRMED'}
        keywords = self.indexing.extractor.extract(query)
        if self.bags is None:
            raise SearchError('SEARCH_SCHEMA_UNAVAILABLE')
        self.bags.ensure_ready()
        vector = None
        if method in ('vector', 'hybrid'):
            # Loading a local model is distinct from executing a bounded query.
            # Preserve the query deadline (including prior schema work), but do not
            # spend it on first-use imports/weights. These checks are cooperative,
            # not preemption of a blocking model call; HTTP admission is bounded.
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
            started += warmup_seconds
            dimension = self.settings.index_embedding_dimension
            vector = (_query_context.encode(embedding, encoder, query, dimension) if _query_context is not None
                      else validate_vector(encoder(query), dimension))
            self._deadline(started)
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
        by_bag, bow, semantic, snapshots, ready_count, source_chars = {}, {}, {}, {}, 0, 0
        for version_id in sorted({bag.resource_version_id for bag in candidates}):
            self._deadline(started)
            with self.indexing.uow() as repo:
                snapshot = self._ready_snapshot(repo, version_id)
                allowed = self._allowed_units(repo, snapshot, access) if snapshot else set()
            if snapshot is None:
                continue
            source_chars += sum(len(unit['text']) for unit in snapshot[2])
            if source_chars > self.settings.index_max_source_chars:
                raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
            snapshots[version_id] = snapshot
            roots, snippets = self._snippets(snapshot, keywords, started, allowed)
            for bag in (bag for bag in candidates if bag.resource_version_id == version_id):
                self._deadline(started)
                # Candidate may have been fetched before a concurrent invalidation.
                current = self.bags.get_bag(bag.id)
                if not current or current != bag:
                    raise SearchError('SEARCH_INDEX_STALE', 409)
                if bag.id not in roots or bag.id not in snippets:
                    continue
                ready_count += 1
                score, matched = binary_score(keywords, bag.keywords)
                if score > 0:
                    bow[bag.id] = score
                if vector is not None:
                    value = cosine(vector, bag.embedding)
                    if value > 0:
                        semantic[bag.id] = value
                chunk = snippets[bag.id][1]
                by_bag[bag.id] = (bag, chunk, matched,
                    representation_mode(snapshot[2], {bag.id: roots[bag.id]}))
        scores = bow if method == 'bow' else semantic if method == 'vector' else rrf(bow, semantic)
        grouped, matches = {}, {}
        for bag_id in sorted(scores, key=lambda key: (-scores[key], key)):
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
                'score_components': {key: branch[bag_id] for key, branch in
                                     [('bow', bow), ('vector', semantic)] if bag_id in branch},
                'snippet': chunk['text'][:400], 'page_from': chunk['page_from'], 'page_to': chunk['page_to'],
                'content_unit_id': chunk['content_unit_id'], 'context_id': token,
                'context_url': f'/api/v1/chunks/{token}/context',
            }
        # Recheck metadata and snapshots after ranking; never publish a known stale vector.
        if grouped:
            with self.indexing.uow() as repo:
                fresh_ids = {row['version_id'] for row in self._metadata(repo, filters, access,
                    self.settings.search_max_bags + 1)}
                for version_id in {item['resource_version_id'] for item in grouped.values()}:
                    fresh = self._ready_snapshot(repo, version_id)
                    if version_id not in fresh_ids or fresh is None or fresh[3] != snapshots[version_id][3]:
                        raise SearchError('SEARCH_INDEX_STALE', 409)
                    allowed = self._allowed_units(repo, fresh, access)
                    if any(not set(bag_structure(fresh[2])[item['bag_id']]) <= allowed
                           for item in grouped.values() if item['resource_version_id'] == version_id):
                        raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
        self._deadline(started)
        items = sorted(grouped.values(), key=lambda item: (-item['score'], item['resource_id']))
        for item in items:
            item['matched_bag_count'] = matches[item['resource_id']]
        total, offset = len(items), (page - 1) * size
        return {'query': query, 'method': method, 'page': page, 'size': size, 'total': total,
                'total_pages': (total + size - 1) // size, 'items': items[offset:offset+size],
                'ranking_algorithm': {'bow': 'binary-keyword-cosine-v1', 'vector': 'bag-cosine-v1',
                                      'hybrid': 'rrf-v1'}[method],
                'representation_unit': 'content_bag', 'ready_bag_count': ready_count, 'access_scope': access.scope,
                'message': 'QUERY_NO_KEYWORDS' if not keywords and method == 'bow' else
                           'NO_READY_INDEX' if metadata and not snapshots else
                           'NO_ACCESSIBLE_CONTENT' if not ready_count else 'OK'}

    def authorize_content_version(self, version_id, *, access=None):
        """Same whole-content rights as File/Context, without requiring an index.

        Local processing/index previews must remain usable before INDEX_BUILD.
        This does not weaken the existing file-viewer INDEX READY gate.
        """
        access = access or SearchAccess()
        if access.scope != 'local_demo':
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
        if access.scope != 'local_demo':
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
        if access.scope != 'local_demo':
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
                    'page_to': unit['page_to'], 'text': text, 'selected': sequence == ordinal})
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

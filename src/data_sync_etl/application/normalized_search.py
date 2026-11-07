"""Read-only BGE shortlist from fresh normalized text; verify winning Bags in full.

This independent representation avoids transferring every meaning vector before
ranking. It never treats projected/partial Bag BSON as a valid index artifact.
"""

from time import monotonic

from data_sync_etl.application.content_bags import bag_structure, representation_mode
from data_sync_etl.application.search_runtime import CURRENT_SEARCH
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.lexical import bm25, lexical_document, query_terms, snippet_text
from data_sync_etl.domain.search import SearchError, cosine, rrf
from data_sync_etl.domain.search_context import encode_context
from data_sync_etl.logging import event


class NormalizedContentSearch:
    def __init__(self, search, vectors):
        self.search, self.vectors = search, vectors

    def scan(self, filters, access, checkpoint):
        service, indexing = self.search, self.search.indexing
        with indexing.uow() as repo:
            rows = service._metadata(repo, filters, access, service.settings.search_max_bags + 1)
        if len(rows) > service.settings.search_max_bags:
            raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
        rows = service._authorized_metadata(rows, access)
        versions = {row['version_id']: row for row in rows}
        reader = getattr(indexing.reader, 'normalized_phase', None)
        units = (reader(list(versions), max_chars=service.settings.index_max_source_chars)
                 if callable(reader) else indexing.reader.units)
        with indexing.uow() as repo:
            phase_factory = getattr(type(repo), 'search_authority_phase', None)
            phase = (repo.search_authority_phase(list(versions), max_chars=service.settings.index_max_source_chars)
                     if callable(phase_factory) else None)
        snapshots, allowed, chars = {}, {}, 0
        with indexing.uow() as repo:
            repo = phase.bind(repo) if phase else repo
            for key in versions:
                checkpoint()
                runs = indexing._runs(repo, key)
                if not runs or runs[0]['status'] != 'COMPLETED':
                    continue
                try:
                    snapshot = indexing._snapshot(repo, key, text_reader=repo.search_unit_texts,
                                                  content_reader=units)
                except IndexingError as exc:
                    if exc.code not in ('CONTENT_NOT_READY', 'EMPTY_CONTENT'):
                        raise
                    continue
                if snapshot[3] != runs[0]['input_fingerprint']:
                    continue
                chars += sum(len(row['text']) for row in snapshot[2])
                if chars > service.settings.index_max_source_chars:
                    raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
                snapshots[key] = snapshot
                allowed[key] = service._allowed_units(repo, snapshot, access)
        return versions, snapshots, allowed

    def retrieve(self, query, method, filters, access, *, limit):
        service, settings = self.search, self.search.settings
        started = monotonic()
        def checkpoint():
            service._deadline(started)
        versions, snapshots, allowed = self.scan(filters, access, checkpoint)
        event(stage='normalized_search', action='INITIAL_READ', coverage={
            'versions': len(snapshots), 'seconds': round(monotonic() - started, 3)})
        chunks, roots, representations = {}, {}, {}
        for version, snapshot in snapshots.items():
            structure = bag_structure(snapshot[2])
            representations[version] = structure
            membership = {unit: root for root, leaves in structure.items()
                          if root in allowed[version] and set(leaves) <= allowed[version] for unit in leaves}
            for chunk in service.indexing._preview(snapshot):
                unit = chunk['content_unit_id']
                if unit not in membership:
                    continue
                key = chunk['id']
                chunks[key] = (version, chunk)
                roots[key] = membership[unit]
        if len(chunks) > settings.search_normalized_max_chunks:
            raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
        lexical, dense = {}, {}
        if method in ('bow', 'hybrid'):
            lexical = bm25(query, {key: lexical_document([chunk['text']])
                                  for key, (_, chunk) in chunks.items()},
                           dictionary=service.dictionary, policy=settings.search_expansion,
                           min_coverage=settings.search_bow_min_coverage)
        if method in ('vector', 'hybrid') and chunks:
            with service._inference():
                embedding = self.vectors.embedding
                identity = embedding.model_identity()
                keys = list(chunks)
                # Original query, actual full chunks, no translations or keyword
                # rewrite. Pure vector reuse requires identical text AND model.
                vectors = self.vectors.encode([chunks[key][1]['text'] for key in keys], checkpoint=checkpoint)
                query_vector = embedding.embed_queries([query])[0]['vector']
                if identity != embedding.model_identity():
                    raise IndexingError('SEMANTIC_MODEL_IDENTITY_CHANGED', 503)
                for key, vector in zip(keys, vectors, strict=True):
                    score = cosine(query_vector, vector)
                    if method == 'vector' or score >= settings.search_normalized_min_similarity:
                        dense[key] = score
        checkpoint()
        scores = lexical if method == 'bow' else dense if method == 'vector' else rrf(lexical, dense)
        event(stage='normalized_search', action='SCORED', coverage={
            'chunks': len(chunks), 'matches': len(scores), 'seconds': round(monotonic() - started, 3)})
        selected = {}
        for key in sorted(scores, key=lambda key: (-scores[key], key)):
            version = chunks[key][0]
            selected.setdefault(version, key)
        # A bounded Top-K, not a lexical prefilter: ALL accessible chunks were
        # scored. Exact-total is explicitly false if remaining hits are not read.
        winners = dict(list(selected.items())[:limit])
        bag_phase = None
        if winners and callable(getattr(type(service.bags), 'search_phase_from_candidates', None)):
            bags = service.bags.search_root_candidates([roots[key] for key in winners.values()], list(winners),
                model=f'{service.indexing.embedding.model_id}@{service.indexing.embedding.version}')
            if len(bags) > settings.search_max_bags:
                raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
            bag_phase = service.bags.search_phase_from_candidates(list(winners), bags,
                limit=settings.search_max_bags + 1, max_chars=settings.index_max_source_chars)
        event(stage='normalized_search', action='ARTIFACT_READ', coverage={
            'versions': len(winners), 'seconds': round(monotonic() - started, 3)})
        if service.graph_advisor is not None and settings.neo4j_search_enabled:
            call = CURRENT_SEARCH.get()
            # Preserve the existing Shadow/Active identity-parity advisor before
            # final source/PG/Mongo checks. It never supplies ranking or grants.
            service.graph_advisor.inspect([
                {'bag_id': roots[key], 'resource_id': versions[version]['resource_id'],
                 'resource_version_id': version} for version, key in winners.items()],
                remaining=call.remaining() if call is not None else None)
        # Fresh normalized authority for the entire corpus, including nonhits.
        fresh_versions, fresh, fresh_allowed = self.scan(filters, access, checkpoint)
        if (versions != fresh_versions or snapshots.keys() != fresh.keys() or any(
                fresh[key][3] != snapshots[key][3] or fresh_allowed[key] != allowed[key] for key in snapshots)):
            raise SearchError('SEARCH_INDEX_STALE', 409)
        items, verified = [], 0
        # The small winner set still undergoes the original FULL BSON Bag,
        # generation, keyword, meaning, source hash and index proof checks.
        for version, key in winners.items():
            checkpoint()
            with service.indexing.uow() as repo:
                if bag_phase is None:
                    snapshot = service._ready_snapshot(repo, version, batch_texts=True, batch_bags=True)
                    if (snapshot is None or snapshot[3] != fresh[version][3]
                            or service._allowed_units(repo, snapshot, access) != fresh_allowed[version]):
                        raise SearchError('SEARCH_INDEX_STALE', 409)
                else:
                    snapshot = fresh[version]
                    runs = service.indexing._runs(repo, version)
                    if not runs or runs[0]['status'] != 'COMPLETED' or runs[0]['input_fingerprint'] != snapshot[3]:
                        raise SearchError('SEARCH_INDEX_STALE', 409)
                    service.indexing._verified(snapshot, runs[0], bag_reader=bag_phase,
                                              selected_roots=[roots[key]])
            chunk, root = chunks[key][1], roots[key]
            node = next(row for row in snapshot[2] if row['id'] == root)
            token = encode_context(version, chunk['content_unit_id'], chunk['unit_chunk_sequence'], snapshot[3])
            row = versions[version]
            verified += 1
            items.append({**{name: value for name, value in row.items() if name != 'version_id'},
                'resource_version_id': version, 'bag_id': root, 'bag_title': node['title'],
                'unit_type': node['unit_type'], 'representation_mode': representation_mode(snapshot[2], {root: representations[version][root]}),
                'matched_keywords': [], 'matched_bag_count': 1, 'score': scores[key],
                'score_components': {name: branch[key] for name, branch in [('bow', lexical), ('vector', dense)] if key in branch},
                'snippet': snippet_text(chunk['text'], set(query_terms(query)), query=query,
                                        dictionary=service.dictionary, policy=settings.search_expansion),
                'page_from': chunk['page_from'], 'page_to': chunk['page_to'],
                'content_unit_id': chunk['content_unit_id'], 'context_id': token,
                'context_url': f'/api/v1/chunks/{token}/context', 'can_view_file': False})
        checkpoint()
        event(stage='normalized_search', action='VERIFIED', coverage={
            'versions': len(winners), 'seconds': round(monotonic() - started, 3)})
        return {'query': query, 'method': method, 'page': 1, 'size': limit, 'items': items,
                'ready_bag_count': verified, 'representation_unit': 'content_bag', 'access_scope': access.scope,
                'ranking_algorithm': ('fresh-normalized-bge-shortlist-nearest-v1' if method == 'vector'
                                      else 'fresh-normalized-bge-shortlist-v1'), 'message': 'OK',
                'total_exact': len(selected) <= limit,
                'retrieval_diagnostics': {'representation': 'normalized-chunk-bge-m3',
                    'scored_chunks': len(chunks), 'verified_versions': len(winners),
                    'eligible_versions': len(versions), 'normalized_versions': len(snapshots)}}

"""Bounded, explicit reconciliation and read-only metadata retrieval."""

from time import monotonic

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.indexing import IndexingError, validate_vector
from data_sync_etl.domain.lexical import LexicalDocument, bm25, snippet_text, surface_tokens
from data_sync_etl.domain.metadata_search import (
    GENERATION,
    SEARCH_FIELDS,
    TEMPLATE,
    lexical_fields,
    metadata_allowed,
    projection,
)
from data_sync_etl.domain.search import SearchError, cosine, ranks, rrf
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.ports.metadata_search import MetadataIndexPort


class MetadataSearchService:
    def __init__(self, uow, documents, store: MetadataIndexPort, embedding, settings):
        self.uow, self.documents, self.store = uow, documents, store
        self.embedding, self.settings = embedding, settings

    def prune(self, *, after='', limit=100, apply=False):
        """Explicit bounded tombstones for deleted/superseded versions; resumable.

        An unavailable authority propagates instead of granting permission to
        invalidate an unknown version. No collection deletion or source access.
        """
        if not 1 <= limit <= 200:
            raise IndexingError('METADATA_CANDIDATE_LIMIT', 422)
        self.store.ensure_ready()
        rows = self.store.page(after, limit=limit + 1)
        report = dict(status='READY' if apply else 'DRY_RUN', scanned=0, would_invalidate=0, invalidated=0,
                      next_after_version_id=rows[limit - 1]['resource_version_id'] if len(rows) > limit else None)
        for row in rows[:limit]:
            report['scanned'] += 1
            live = self.snapshots({'resource_id': row['resource_id']}, SearchAccess('authenticated_admin'))
            if row['resource_version_id'] not in live:
                report['would_invalidate'] += 1
                if apply:
                    self.store.invalidate(row['resource_version_id'])
                    report['invalidated'] += 1
        return report

    def snapshots(self, filters, access, *, checkpoint=lambda: None):
        cap = self.settings.search_metadata_max_resources
        with self.uow() as repo:
            rows = repo.metadata_snapshots({k: v for k, v in filters.items() if k != 'domain'}, access, limit=cap + 1)
            if len(rows) > cap:
                raise SearchError('METADATA_CANDIDATE_LIMIT', 422)
            result = {}
            for row, resource, version, curriculum, imports in rows:
                checkpoint()
                if not metadata_allowed(resource, version, access):
                    continue
                if resource.get('source_origin') == 'POSTGRESQL':
                    if (len(imports) != 1 or imports[0]['status'] not in ('METADATA_ONLY', 'IMPORTED', 'PROCESSING')
                            or imports[0]['last_error_code'] not in (None, 'UNSUPPORTED_SOURCE_FORMAT')
                            or not imports[0]['source_resource_active']
                            or imports[0]['source_lifecycle_status'] not in self.settings.source_file_allowed_statuses
                            or imports[0]['resource_id'] != resource['id']
                            or imports[0]['source_version_number'] != version['version_number']
                            or imports[0]['source_hash'] != version['source_hash']):
                        continue
                elif resource.get('source_origin') != 'LOCAL' or imports:
                    continue
                extension = None
                if resource.get('mongo_id'):
                    if self.documents is None:
                        raise IndexingError('METADATA_AUTHORITY_UNAVAILABLE')
                    try:
                        reader = getattr(self.documents, 'get_resource_search', self.documents.get_resource)
                        extension = reader(resource['mongo_id'])
                    except DomainError:
                        raise IndexingError('METADATA_AUTHORITY_UNAVAILABLE') from None
                    if extension is not None and extension.id != resource['mongo_id']:
                        raise IndexingError('METADATA_AUTHORITY_UNAVAILABLE')
                snap = projection(resource, version, curriculum, extension)
                if snap:
                    result[version['id']] = (row, snap)
            return result

    def reconcile(self, filters=None, *, apply=False, dense=False, access=None):
        started = monotonic()
        self.store.ensure_ready()
        access = access or SearchAccess('authenticated_admin')
        snapshots = self.snapshots(filters or {}, access)
        old = self.store.get_many(list(snapshots))
        identity = self.embedding.model_identity() if dense else None
        report = dict(status='READY' if apply else 'DRY_RUN', resources=len(snapshots),
                      ready=0, unchanged=0, embeds=0, writes=0, stale=0, dense_failed=0)
        pending = []
        for key, (_, snap) in snapshots.items():
            previous = old.get(key, {})
            # A lexical-only reconcile never destroys a current compatible dense
            # vector. Changing model/revision invalidates it without comparing 384D.
            current_identity = self.embedding.model_identity()
            dense_same = (previous.get('dense_input_fingerprint') == snap['dense_input_fingerprint']
                          and previous.get('model_identity') == current_identity
                          and previous.get('dense_status') == 'READY')
            if dense_same:
                validate_vector(previous.get('vector', []), 1024)
            if (previous.get('projection_fingerprint') == snap['projection_fingerprint']
                    and previous.get('lexical_fingerprint') == snap['lexical_fingerprint']
                    and (not dense or dense_same)):
                report['unchanged'] += 1
                report['ready'] += 1
                continue
            record = {**snap, 'status': 'READY', 'updated_at': now(),
                      'lexical': {name: doc.model_dump() for name, doc in lexical_fields(snap).items()},
                      'vector': previous.get('vector') if dense_same else None,
                      'model_identity': current_identity if dense_same else identity,
                      'dense_status': 'READY' if dense_same else 'NOT_INDEXED'}
            pending.append((key, record, dense and not dense_same))
        report['would_write'] = len(pending)
        report['would_embed'] = sum(needs_dense for _, _, needs_dense in pending)
        if apply:
            batch_size = min(16, self.settings.semantic_batch_size)
            for offset in range(0, len(pending), batch_size):
                if monotonic() - started > self.settings.index_timeout_seconds:
                    raise IndexingError('METADATA_INDEX_TIMEOUT')
                batch = pending[offset:offset + batch_size]
                encoding = [row for row in batch if row[2]]
                if encoding:
                    try:
                        vectors = self.embedding.embed_documents([row[1]['text'] for row in encoding])
                        if len(vectors) != len(encoding):
                            raise IndexingError('INVALID_EMBEDDING')
                        checked = [validate_vector(row['vector'], 1024) for row in vectors]
                        for (_, record, _), vector in zip(encoding, checked, strict=True):
                            record.update(vector=vector, dense_status='READY')
                            report['embeds'] += 1
                    except IndexingError:
                        for _, record, _ in encoding:
                            record.update(vector=None, dense_status='FAILED')
                            report['dense_failed'] += 1
                fresh = self.snapshots(filters or {}, access)
                for key, record, _ in batch:
                    if key not in fresh or fresh[key][1]['projection_fingerprint'] != record['projection_fingerprint']:
                        self.store.invalidate(key)
                        report['stale'] += 1
                        continue
                    self.store.upsert(record)
                    report['writes'] += 1
                    report['ready'] += 1
        if report['dense_failed'] or report['stale']:
            report['status'] = 'PARTIAL'
        report['wall_seconds'] = monotonic() - started
        return report

    def retrieve(self, query, method, filters, access, *, dictionary, policy, checkpoint=lambda: None):
        self.store.ensure_ready()
        snapshots = self.snapshots(filters, access, checkpoint=checkpoint)
        stored = self.store.get_many(list(snapshots))
        verified = {}
        for key, record in stored.items():
            checkpoint()
            current = snapshots.get(key)
            if (not current or record.get('status') != 'READY' or record.get('generation') != GENERATION
                    or record.get('template') != TEMPLATE):
                continue
            snap = current[1]
            if any(record.get(field) != snap[field] for field in (
                    'projection_fingerprint', 'lexical_fingerprint', 'dense_input_fingerprint', 'fields', 'text')):
                continue
            if record.get('lexical') != {name: doc.model_dump() for name, doc in lexical_fields(snap).items()}:
                continue
            verified[key] = record
        lexical, dense, matched = {}, {}, {}
        if method in ('bow', 'hybrid'):
            fields = []
            for field in SEARCH_FIELDS:
                documents = {key: LexicalDocument.model_validate(row['lexical'][field]) for key, row in verified.items()}
                scores = bm25(query, documents, dictionary=dictionary, policy=policy, domain=filters.get('domain'))
                fields.append(scores)
                for key in scores:
                    matched.setdefault(key, []).append(field.upper())
            lexical = rrf(*fields)
        dense_status = 'NOT_REQUESTED'
        if method in ('vector', 'hybrid'):
            identity = self.embedding.model_identity()
            eligible = {key: row for key, row in verified.items()
                        if row.get('dense_status') == 'READY' and row.get('model_identity') == identity}
            dense_status = 'NOT_READY'
            if eligible:
                try:
                    query_vector = validate_vector(self.embedding.embed_queries([query])[0]['vector'], 1024)
                    for key, row in eligible.items():
                        checkpoint()
                        dense[key] = cosine(query_vector, validate_vector(row['vector'], 1024))
                    dense_status = 'READY'
                except IndexingError as exc:
                    dense = {}
                    dense_status = exc.code
        scores = lexical if method == 'bow' else dense if method == 'vector' else rrf(lexical, dense)
        items = []
        for key in sorted(scores, key=lambda key: (-scores[key], key)):
            row, snap = snapshots[key]
            fields = matched.get(key, [])
            field = fields[0].lower() if fields else next(name for name in ('description', 'title', 'tags') if snap['fields'].get(name))
            text = snap['fields'][field]
            items.append({**{name: value for name, value in row.items() if name != 'version_id'},
                'resource_version_id': key, 'match_origin': 'METADATA', 'matched_fields': fields,
                'resource_format': snap['resource_format'],
                'evidence_kind': 'METADATA_FIELD', 'evidence_text': text[:600],
                'bag_id': None, 'bag_title': None, 'unit_type': None, 'representation_mode': 'metadata',
                'matched_keywords': [], 'matched_bag_count': 0, 'score': scores[key], 'score_components': {},
                'snippet': snippet_text(text, set(surface_tokens(query)), query=query),
                'page_from': None, 'page_to': None, 'content_unit_id': None, 'context_id': None,
                'context_url': None, 'can_view_file': False})
        status = {'lexical': 'STALE' if len(stored) > len(verified) else 'READY', 'dense': dense_status}
        return items, snapshots, status, {'metadata_lexical': lexical, 'metadata_dense': dense}

    def final_validate(self, items, snapshots, filters, access, *, checkpoint=lambda: None, content=False):
        fresh = self.snapshots(filters, access, checkpoint=checkpoint)
        if not content:
            # A revoked/nonhit document can change corpus IDF and therefore the
            # ranking of every survivor. Do not publish old BM25/cosine ranks
            # after ANY authoritative corpus change, even outside visible Top-K.
            if (fresh.keys() != snapshots.keys() or any(
                    fresh[key][1]['projection_fingerprint'] != snapshots[key][1]['projection_fingerprint']
                    for key in snapshots)):
                return []
            return items
        return [item for item in items if (content and item['resource_version_id'] not in snapshots) or (
            item['resource_version_id'] in fresh and item['resource_version_id'] in snapshots
            and fresh[item['resource_version_id']][1]['projection_fingerprint']
            == snapshots[item['resource_version_id']][1]['projection_fingerprint'])]


def fuse_results(content, metadata, branches=None):
    """RRF votes once per version per family; one card per real Resource ID."""
    representatives = {}
    for kind, rows in (('CONTENT', content), ('METADATA', metadata)):
        for row in rows:
            representatives.setdefault(row['resource_version_id'], {}).setdefault(kind, row)
    if branches is None:
        branches = {'content': {row['resource_version_id']: row['score'] for row in content},
                    'metadata': {row['resource_version_id']: row['score'] for row in metadata}}
    # Only candidates surviving final validation may influence returned ranks.
    branches = {name: {key: score for key, score in scores.items() if key in representatives}
                for name, scores in branches.items()}
    scores = rrf(*branches.values())
    grouped = {}
    for key in sorted(scores, key=lambda key: (-scores[key], key)):
        sources = representatives[key]
        row = dict(sources.get('CONTENT') or sources['METADATA'])
        if row['resource_id'] in grouped:
            continue
        if 'CONTENT' in sources:
            row.update(match_origin='BOTH' if 'METADATA' in sources else 'CONTENT',
                       matched_fields=['CONTENT'], evidence_kind='CONTENT_CHUNK', evidence_text=row.get('snippet', ''))
            if 'METADATA' in sources:
                row['matched_fields'] += sources['METADATA']['matched_fields']
        row.update(score=scores[key], score_components={name: 1 / (60 + ranks(branch)[key])
                   for name, branch in branches.items() if key in branch})
        grouped[row['resource_id']] = row
    return list(grouped.values())

"""Transient Chunk views and version-locked keyword/ContentBag indexing."""

from time import monotonic

from data_sync_etl.application.content_bags import (
    ContentBagBuilder,
    StructuredUnit,
    bag_structure,
    representation_mode,
)
from data_sync_etl.application.leaf_keywords import SaveLeafKeywords
from data_sync_etl.application.processing_state import processing_state
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.indexing import IndexingError, digest, validate_vector, word_windows
from data_sync_etl.domain.keywords import UnigramKeywords
from data_sync_etl.domain.lexical import ANALYZER_VERSION, REPRESENTATION_VERSION, lexical_document
from data_sync_etl.domain.meaning_bags import VERSION as MEANING_VERSION
from data_sync_etl.domain.meaning_bags import verify_meanings
from data_sync_etl.domain.mongo_documents import normalize_keywords
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.ports.indexing import IndexContentPort, IndexEmbeddingPort
from data_sync_etl.ports.mongo_foundation import (
    ContentBagPort,
    KeywordExtractorPort,
)


class IndexingService:
    def __init__(self, uow, reader: IndexContentPort | None, embedding: IndexEmbeddingPort, settings,
                 *, bags: ContentBagPort | None = None, extractor: KeywordExtractorPort | None = None):
        self.uow, self.reader, self.embedding, self.settings = uow, reader, embedding, settings
        self.bags, self.extractor = bags, extractor or UnigramKeywords()
        self.source_content_authorizer = None

    def _authorize_source_index(self, version_id):
        if (self.source_content_authorizer is not None
                and not self.source_content_authorizer(
                    version_id, access=SearchAccess(scope='authenticated_admin'))):
            raise IndexingError('SOURCE_CAPABILITY_BLOCKED', 403)

    def _version(self, repo, version_id):
        version = repo.get('resource_versions', version_id)
        resource = repo.get('master_learning_resources', version['resource_id']) if version else None
        if not resource or not resource['is_active'] or resource_deleted(resource):
            raise IndexingError('VERSION_NOT_FOUND', 404)
        jobs = repo.find('ocr_jobs', {'resource_version_id': version_id}, order=('-created_at', '-id'), limit=1)
        return version, processing_state(repo, jobs[0]) if jobs else 'NOT_STARTED'

    def _runs(self, repo, version_id):
        return repo.find('etl_stage_runs', {'resource_version_id': version_id, 'stage': 'INDEX_BUILD'},
                         order=('-created_at', '-id'))

    def _snapshot(self, repo, version_id, *, text_reader=None):
        version, state = self._version(repo, version_id)
        if state != 'CONTENT_READY':
            raise IndexingError('CONTENT_NOT_READY')
        if self.reader is None:
            raise IndexingError('BLOCKED_INDEX_SCHEMA', 503)
        units = repo.find('content_units', {'resource_version_id': version_id},
                         order=('sequence_no', 'id'), limit=self.settings.index_max_units + 1)
        if len(units) > min(self.settings.index_max_units, 10000):
            raise IndexingError('INDEX_LIMIT_EXCEEDED')
        projections = repo.find('etl_stage_runs', {'resource_version_id': version_id, 'stage': 'MONGO_PROJECTION'},
                                order=('-created_at', '-id'))
        generation, docs = len(projections), []
        for offset in range(0, len(units) + 1, 100):
            batch = self.reader.units(version_id, limit=100, offset=offset)
            docs.extend(batch)
            if len(batch) < 100:
                break
        by_id = {doc.id: doc for doc in docs}
        if not projections or len(docs) != len(units) or len(by_id) != len(docs):
            raise IndexingError('CONTENT_NOT_READY')
        # HTTP Search alone opts in. All other callers retain point-read timing.
        texts = text_reader([unit['id'] for unit in units]) if text_reader is not None else None
        content, total_chars = [], 0
        for unit in units:
            body = texts.get(unit['id']) if texts is not None else repo.get('content_unit_texts', unit['id'])
            doc = by_id.get(unit['id'])
            if (not body or not doc or unit['mongo_id'] != unit['id'] or doc.content_unit_id != unit['id']
                    or not doc.is_current or doc.projection_generation != generation or doc.resource_version_id != version_id
                    or doc.text != body['text'] or doc.page_from != unit['page_from'] or doc.page_to != unit['page_to']):
                raise IndexingError('CONTENT_NOT_READY')
            total_chars += len(body['text'])
            if total_chars > self.settings.index_max_source_chars:
                raise IndexingError('INDEX_LIMIT_EXCEEDED')
            content.append({**unit, 'text': body['text']})
        if not any(unit['text'].strip() for unit in content):
            raise IndexingError('EMPTY_CONTENT')
        children = {u['parent_unit_id'] for u in content if u['parent_unit_id'] is not None}
        keywords = {u['id']: self.extractor.extract(u['text']) for u in content if u['id'] not in children}
        try:
            membership = bag_structure(content)
        except DomainError:
            membership = {'invalid_hierarchy': []}  # Preview remains independent; _roots rejects indexing.
        deletion_revision = repo.get('master_learning_resources', version['resource_id']).get('deletion_revision', 0)
        fingerprint = digest({
            **({'deletion_revision': deletion_revision} if deletion_revision else {}),
            'version': version_id, 'source_hash': version['source_hash'], 'projection': projections[0]['id'], 'generation': generation,
            'units': [{k: u[k] for k in ('id', 'parent_unit_id', 'unit_type', 'title', 'sequence_no',
                                        'text', 'page_from', 'page_to', 'review_status', 'retrieval_eligible')}
                      | ({'source_locator': u['source_locator']} if u.get('source_locator') else {}) for u in content],
            'keywords': keywords, 'extractor': self.extractor.version, 'bag_algorithm': 'pg-structured-leaf-fallback-v2',
            'membership': membership, 'lexical': [REPRESENTATION_VERSION, ANALYZER_VERSION],
            **({'meanings': [MEANING_VERSION, self.settings.index_meaning_group_threshold]}
               if self.settings.index_meaning_bags_enabled else {}),
            'chunk_algorithm': 'word_window_v1', 'size': self.settings.chunk_size_words,
            'overlap': self.settings.chunk_overlap_words,
            'model': [self.embedding.model_id, self.embedding.version, self.embedding.dimension],
        })
        return version, generation, content, fingerprint, by_id, keywords

    @staticmethod
    def _roots(snapshot):
        try:
            roots = bag_structure(snapshot[2])
        except DomainError:
            raise IndexingError('BAG_STRUCTURE_INVALID') from None
        if not roots:
            raise IndexingError('BAG_STRUCTURE_UNAVAILABLE')
        # Numeric-only leaves remain available in Chunk preview. A root needs
        # real keywords, but one keywordless row must not block the whole file.
        roots = {root: leaves for root, leaves in roots.items()
                 if any(snapshot[5][leaf] for leaf in leaves)}
        if not roots:
            raise IndexingError('LEAF_KEYWORDS_UNAVAILABLE')
        return roots

    def _runtime(self):
        if not self.embedding.available():
            raise IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503)
        if self.bags is None:
            raise IndexingError('BLOCKED_INDEX_SCHEMA', 503)
        if self.bags.dimension != self.embedding.dimension or self.embedding.dimension != self.settings.index_embedding_dimension:
            raise IndexingError('INVALID_EMBEDDING', 503)
        if (not self.embedding.model_id.strip() or not self.embedding.version.strip()
                or len(f'{self.embedding.model_id}@{self.embedding.version}') > 255):
            raise IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503)
        self.bags.ensure_ready()
        self.bags.ensure_lexical_ready()
        if self.settings.index_meaning_bags_enabled:
            self.bags.ensure_meanings_ready()

    def _verified(self, snapshot, run, *, expected_bags=None, bag_reader=None):
        roots = self._roots(snapshot)
        source_fp = self.reader.content_fingerprint(snapshot[0]['id'], generation=snapshot[1])
        try:
            bags = (bag_reader(list(roots), version_id=snapshot[0]['id'], generation=snapshot[1])
                    if bag_reader is not None else None)
        except DomainError:
            raise IndexingError('INDEX_ARTIFACT_INVALID', 503) from None
        nodes, result = {u['id']: u for u in snapshot[2]}, []
        for root, leaves in roots.items():
            keywords = normalize_keywords([k for leaf in leaves for k in snapshot[5][leaf]])
            try:
                bag = bags.get(root) if bags is not None else self.bags.get_bag(root)
            except DomainError:
                raise IndexingError('INDEX_ARTIFACT_INVALID', 503) from None
            if (not bag or run['input_fingerprint'] != snapshot[3] or bag.content_fingerprint != source_fp
                    or expected_bags is not None and bag != expected_bags.get(root)
                    or bag.resource_version_id != snapshot[0]['id'] or bag.projection_generation != snapshot[1]
                    or bag.unit_type != nodes[root]['unit_type'] or bag.title != nodes[root]['title']
                    or bag.keywords != keywords or bag.embedding_text != ' | '.join(keywords)
                    or bag.lexical != lexical_document([snapshot[4][leaf].text for leaf in leaves], nodes[root]['title'])
                    or bag.embedding_model != f'{self.embedding.model_id}@{self.embedding.version}'
                    or any(snapshot[4][leaf].keywords != snapshot[5][leaf] for leaf in leaves)):
                raise IndexingError('INDEX_ARTIFACT_INVALID', 503)
            validate_vector(bag.embedding, self.embedding.dimension)
            if self.settings.index_meaning_bags_enabled and not verify_meanings(
                    bag.meanings, [(leaf, snapshot[4][leaf].text) for leaf in leaves], root, nodes[root]['title'],
                    bag.embedding_model, self.embedding.dimension, self.settings.index_meaning_group_threshold):
                raise IndexingError('INDEX_ARTIFACT_INVALID', 503)
            result.append({'id': root, 'unit_type': bag.unit_type, 'title': bag.title, 'keywords': bag.keywords,
                           'representation_mode': representation_mode(snapshot[2], {root: leaves}),
                           'embedding_text': bag.embedding_text, 'embedding_model': bag.embedding_model,
                           'embedding_dimension': self.embedding.dimension, 'vector_ready': True})
        return result

    def enqueue(self, version_id, *, force=False):
        self._authorize_source_index(version_id)
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                with self.uow() as repo:
                    self._version(repo, version_id)
                    inflight = next((r for r in self._runs(repo, version_id) if r['status'] in ('PENDING', 'RUNNING')), None)
                if inflight and not force:
                    return self._queued(version_id, inflight, 'IN_PROGRESS')
                raise IndexingError('INDEX_ALREADY_RUNNING')
            with self.uow() as repo:
                version = repo.get('resource_versions', version_id)
                if version and version.get('lifecycle_status') == 'METADATA_ONLY':
                    raise IndexingError('CONTENT_NOT_AVAILABLE', 409)
                snapshot = self._snapshot(repo, version_id)
                self._roots(snapshot)
                runs = self._runs(repo, version_id)
                inflight = next((r for r in runs if r['status'] in ('PENDING', 'RUNNING')), None)
                if inflight:
                    if force:
                        raise IndexingError('INDEX_ALREADY_RUNNING')
                    return self._queued(version_id, inflight, 'IN_PROGRESS')
                self._runtime()
                if not force and runs:
                    if runs[0]['status'] == 'FAILED':
                        raise IndexingError('REINDEX_REQUIRED')
                    if runs[0]['status'] == 'COMPLETED' and runs[0]['input_fingerprint'] == snapshot[3]:
                        self._verified(snapshot, runs[0])
                        return self._queued(version_id, runs[0], 'ALREADY_INDEXED')
                stage = repo.insert('etl_stage_runs', {
                    'resource_version_id': version_id, 'stage': 'INDEX_BUILD', 'status': 'PENDING',
                    'started_at': now(), 'created_at': now(), 'input_fingerprint': snapshot[3]})
                return self._queued(version_id, stage, 'QUEUED')

    @staticmethod
    def _queued(version_id, run, action):
        return {'resource_version_id': version_id, 'index_run_id': run['id'],
                'index_status': 'READY' if run['status'] == 'COMPLETED' else run['status'],
                'action': action, 'status_url': f'/api/v1/resource-versions/{version_id}/index-status'}

    def once(self, version_id=None):
        with self.uow() as repo:
            runs = (repo.index_candidates() if version_id is None else
                    [run for run in self._runs(repo, version_id) if run['status'] in ('PENDING', 'RUNNING')])
        for run in runs:
            try:
                self._authorize_source_index(run['resource_version_id'])
            except IndexingError as exc:
                with self.uow() as repo:
                    current = repo.get('etl_stage_runs', run['id'], lock=True)
                    if current and current['status'] in ('PENDING', 'RUNNING'):
                        repo.update('etl_stage_runs', run['id'], {
                            'status': 'FAILED', 'finished_at': now(), 'error_message': exc.code})
                continue
            with self.uow.processing_lock(run['resource_version_id']) as acquired:
                if not acquired:
                    continue
                with self.uow() as repo:
                    current = repo.get('etl_stage_runs', run['id'], lock=True)
                    if current['status'] not in ('PENDING', 'RUNNING'):
                        continue
                    version = repo.get('resource_versions', run['resource_version_id'])
                    resource = repo.get('master_learning_resources', version['resource_id'], lock=True) if version else None
                    if not resource or resource_deleted(resource):
                        continue
                    repo.update('etl_stage_runs', run['id'], {'status': 'RUNNING', 'started_at': now()})
                self._build(run)
                return True
        return False

    def _build(self, run):
        version_id, started = run['resource_version_id'], monotonic()
        try:
            with self.uow() as repo:
                snapshot = self._snapshot(repo, version_id)
            roots = self._roots(snapshot)
            if snapshot[3] != run['input_fingerprint']:
                raise IndexingError('INDEX_SOURCE_CHANGED')
            self._runtime()
            self.embedding.prepare()
            saver = SaveLeafKeywords(self.uow, self.reader)
            leaf_ids = sorted({leaf for leaves in roots.values() for leaf in leaves})
            for leaf in leaf_ids:
                self._deadline(started)
                saver.execute(leaf, snapshot[5][leaf])
            with self.uow() as repo:
                current = self._snapshot(repo, version_id)
            if current[3] != snapshot[3]:
                raise IndexingError('INDEX_SOURCE_CHANGED')
            service = self

            class CheckedEmbedding:
                model_id, version, dimension = service.embedding.model_id, service.embedding.version, service.embedding.dimension

                def embed(self, text):
                    service._deadline(started)
                    vector = validate_vector(service.embedding.embed(text), self.dimension)
                    service._deadline(started)
                    return vector

                def embed_many(self, texts):
                    service._deadline(started)
                    encoder = getattr(service.embedding, 'embed_many', None)
                    vectors = encoder(texts) if encoder is not None else [self.embed(text) for text in texts]
                    service._deadline(started)
                    return [validate_vector(vector, self.dimension) for vector in vectors]

            source_fp = self.reader.content_fingerprint(version_id, generation=current[1])
            result = ContentBagBuilder(CheckedEmbedding(), meaning_threshold=(
                self.settings.index_meaning_group_threshold if self.settings.index_meaning_bags_enabled else None)).build(
                version_id, [StructuredUnit(**{k: u[k] for k in StructuredUnit.model_fields}) for u in current[2]],
                [current[4][key] for key in leaf_ids], generation=current[1], content_fingerprint=source_fp)
            if result.status != 'BUILT' or len(result.bags) != len(roots):
                raise IndexingError('LEAF_KEYWORDS_UNAVAILABLE')
            for bag in result.bags:
                self._deadline(started)
                self.bags.upsert_bag(bag)
            with self.uow() as repo:
                fresh = self._snapshot(repo, version_id)
                owner = repo.get('etl_stage_runs', run['id'], lock=True)
                if fresh[3] != snapshot[3] or owner['status'] != 'RUNNING':
                    raise IndexingError('INDEX_SOURCE_CHANGED')
                self._verified(fresh, run)
                self.bags.invalidate_other_roots(version_id, list(roots), fresh[1])
                self._deadline(started)
                repo.update('etl_stage_runs', run['id'], {'status': 'COMPLETED', 'finished_at': now(), 'error_message': None})
        except Exception as exc:
            code = exc.code if isinstance(exc, IndexingError) else 'INDEX_FAILED'
            with self.uow() as repo:
                repo.update('etl_stage_runs', run['id'], {'status': 'FAILED', 'finished_at': now(), 'error_message': code})

    def _deadline(self, started):
        if monotonic() - started > self.settings.index_timeout_seconds:
            raise IndexingError('INDEX_LIMIT_EXCEEDED')

    def status(self, version_id):
        with self.uow() as repo:
            version, processing = self._version(repo, version_id)
            metadata_only = bool(version and version.get('lifecycle_status') == 'METADATA_ONLY')
            runs = self._runs(repo, version_id)
            try:
                snapshot = self._snapshot(repo, version_id) if processing == 'CONTENT_READY' else None
                code = None
            except IndexingError as exc:
                snapshot, code = None, exc.code
        latest = runs[0] if runs else None
        status, summaries, published, mode = 'CONTENT_NOT_READY', [], None, 'unavailable'
        historical_failure = False
        if snapshot:
            status = 'NOT_STARTED'
            try:
                roots = self._roots(snapshot)
                mode = representation_mode(snapshot[2], roots)
                self._runtime()
                if latest:
                    code = latest['error_message'] if latest['status'] == 'FAILED' else None
                    if latest['input_fingerprint'] != snapshot[3]:
                        status, code = 'STALE', 'INDEX_SOURCE_CHANGED'
                    elif latest['status'] == 'COMPLETED':
                        summaries = self._verified(snapshot, latest)
                        status, published = 'READY', latest['id']
                    else:
                        status = latest['status']
                        historical_failure = status == 'FAILED'
                        # _runtime() above describes CURRENT blockers. A failed run's
                        # error is historical and must not lock Reindex after recovery.
            except IndexingError as exc:
                code = exc.code
                status = ('BLOCKED_STRUCTURE' if code in ('BAG_STRUCTURE_UNAVAILABLE', 'BAG_STRUCTURE_INVALID')
                          else 'BLOCKED_RUNTIME' if code in ('BLOCKED_EMBEDDING_RUNTIME', 'BLOCKED_INDEX_SCHEMA', 'INVALID_EMBEDDING')
                          else 'STALE' if code == 'INDEX_ARTIFACT_INVALID' else 'FAILED')
        messages = {
            'CONTENT_NOT_READY': 'Chưa hoàn tất trích xuất và chuẩn hóa nội dung.',
            'NOT_STARTED': 'Chưa xây dựng ContentBag.', 'PENDING': 'Chờ xây dựng ContentBag.',
            'RUNNING': 'Đang trích từ khóa và tạo vector ContentBag.', 'READY': 'ContentBag đã sẵn sàng.',
            'FAILED': 'Xây dựng ContentBag thất bại; có thể tái lập chỉ mục.',
            'STALE': 'Nội dung hoặc cấu hình đã thay đổi; cần xây dựng lại ContentBag.',
            'BLOCKED_STRUCTURE': 'Cấu trúc nội dung không hợp lệ hoặc chưa có leaf phù hợp để xây ContentBag; vẫn có thể xem Chunk.',
            'BLOCKED_RUNTIME': 'Chưa thể xây ContentBag: cần kiểm tra model, số chiều hoặc schema MongoDB.'}
        count = sum(1 for _ in self._preview(snapshot)) if snapshot else 0
        return {
            'resource_version_id': version_id, 'processing_status': processing, 'index_status': status,
            'metadata_only': metadata_only,
            'auto_enabled': self.settings.index_auto_enabled, 'latest_run_id': latest['id'] if latest else None,
            'published_run_id': published, 'chunk_count': count, 'chunk_preview_count': count,
            'representation_unit': 'content_bag', 'chunk_storage': 'transient', 'bag_count': len(summaries), 'bags': summaries,
            'representation_mode': mode,
            'bow_ready': bool(summaries), 'vector_ready': bool(summaries) and status == 'READY',
            'algorithm': 'pg-structured-leaf-fallback-v2', 'chunk_size_words': self.settings.chunk_size_words,
            'chunk_overlap_words': self.settings.chunk_overlap_words, 'embedding_model': self.embedding.model_id or None,
            'embedding_version': self.embedding.version or None,
            'embedding_dimension': self.embedding.dimension if self.embedding.model_id else None,
            'started_at': latest['started_at'] if latest else None, 'finished_at': latest['finished_at'] if latest else None,
            'error_code': code, 'message': messages[status] if historical_failure else {
                'BLOCKED_EMBEDDING_RUNTIME': 'Chưa xác nhận được model embedding local hợp lệ; cần kiểm tra runtime và revision.',
                'BLOCKED_INDEX_SCHEMA': 'Schema MongoDB chưa tương thích ContentBag; cần kiểm tra validator, số chiều và indexes.',
                'INVALID_EMBEDDING': 'Vector hoặc số chiều model/ContentBag không hợp lệ.',
                'LEAF_KEYWORDS_UNAVAILABLE': 'Chưa có từ khóa hợp lệ ở ContentUnit lá để xây ContentBag.',
            }.get(code, messages[status])}

    def enqueue_auto(self, repo, version_id):
        """Inside finalization's transaction/lock; blocked work never becomes an infinite retry."""
        if not self.settings.index_auto_enabled:
            return
        fingerprint = None
        try:
            snapshot = self._snapshot(repo, version_id)
            fingerprint = snapshot[3]
            self._roots(snapshot)
            self._runtime()
            code = None
        except Exception as exc:
            code = exc.code if isinstance(exc, IndexingError) else 'INDEX_UNAVAILABLE'
        if any(r['input_fingerprint'] == fingerprint and (fingerprint is not None or r['error_message'] == code)
               for r in self._runs(repo, version_id)):
            return
        repo.insert('etl_stage_runs', {
            'resource_version_id': version_id, 'stage': 'INDEX_BUILD', 'status': 'FAILED' if code else 'PENDING',
            'input_fingerprint': fingerprint, 'error_message': code, 'started_at': now(), 'created_at': now(),
            'finished_at': now() if code else None})

    def _preview(self, snapshot):
        sequence = 0
        for unit in snapshot[2]:
            for ordinal, (start, end, text) in enumerate(word_windows(
                    unit['text'], self.settings.chunk_size_words, self.settings.chunk_overlap_words), 1):
                sequence += 1
                if sequence > self.settings.index_max_chunks:
                    raise IndexingError('INDEX_LIMIT_EXCEEDED')
                yield {'id': digest([snapshot[0]['id'], unit['id'], ordinal, snapshot[3]])[:30],
                       'content_unit_id': unit['id'], 'chunk_sequence': sequence, 'unit_chunk_sequence': ordinal,
                       'page_from': unit['page_from'], 'page_to': unit['page_to'], 'char_start': start, 'char_end': end,
                       'text': text, 'review_status': unit['review_status'], 'retrieval_eligible': unit['retrieval_eligible'],
                       'leaf_keywords': snapshot[4][unit['id']].keywords or []}

    def chunks(self, version_id, *, offset=0, limit=10, content_unit_id=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise IndexingError('INVALID_INDEX_QUERY', 422)
        with self.uow() as repo:
            _, state = self._version(repo, version_id)
            snapshot = self._snapshot(repo, version_id) if state == 'CONTENT_READY' else None
        items, total = [], 0
        if snapshot:
            for item in self._preview(snapshot):
                if content_unit_id is not None and item['content_unit_id'] != content_unit_id:
                    continue
                if offset <= total < offset + limit:
                    items.append(item)
                total += 1
        return {'resource_version_id': version_id, 'index_run_id': None, 'index_status': 'PREVIEW' if snapshot else 'CONTENT_NOT_READY',
                'representation_status': 'PREVIEW' if snapshot else 'CONTENT_NOT_READY', 'chunk_storage': 'transient',
                'total': total, 'offset': offset, 'limit': limit, 'items': items}

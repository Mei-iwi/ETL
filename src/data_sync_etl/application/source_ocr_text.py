"""OCR/text ports for SOURCE mode. MASTER receives metadata and checksums only."""
from hashlib import sha256

from data_sync_etl.application.source_ocr_runtime import source_owned_id, stable_id
from data_sync_etl.domain.core import DomainError, normalize_text, now


def metadata_without_raw(metadata):
    result = dict(metadata or {})
    if 'paired_segments' in result:
        segments = []
        for segment in result['paired_segments']:
            value = {key: content for key, content in segment.items() if key != 'original_text'}
            if isinstance(segment.get('original_text'), str) and segment.get('translated_text') == segment['original_text']:
                value['translated_text'] = None
                value['_translation_reuses_original'] = True
            segments.append(value)
        result['paired_segments'] = segments
    return result


class SourceOCRText:
    def __init__(self, pipeline, legacy):
        self.pipeline, self.legacy = pipeline, legacy
        self.uow = pipeline.uow

    def unit_identity(self, repo, version_id, sequence):
        state = self.pipeline.state(version_id, repo)
        return ({'id': stable_id('SL', state['identity']['namespace'], version_id, state['generation'], sequence)}
                if state else {})

    def source_version(self, repo, version_id):
        return self.pipeline.state(version_id, repo) is not None

    def processing_state_from_content(self, repo, job, content_reader):
        # HTTP Search supplies a fresh, bounded content phase. Initial and final
        # phases have independent readers; nothing is retained across them.
        return self.processing_state(repo, job, content_reader=content_reader)

    def processing_state(self, repo, job, *, content_reader=None):
        state = self.pipeline.state(job['resource_version_id'], repo)
        if not state:
            if source_owned_id(job['id'], 'SJ'):
                return 'FINALIZATION_FAILED'
            from data_sync_etl.application.processing_state import processing_state
            return processing_state(repo, job, self.legacy)
        if job['id'] != stable_id('SJ', state['identity']['namespace'], state['source_job_id']):
            raise DomainError('SOURCE_OCR_JOB_CONFLICT')
        first = state.get('pages', {}).get('1', {}).get('result_metadata') or {}
        if job['status'] == 'COMPLETED' and first.get('extraction_status') == 'no_extractable_content':
            return 'NO_EXTRACTABLE_CONTENT'
        from data_sync_etl.application.processing_state import processing_state_from_stages
        stages = repo.find('etl_stage_runs', {'resource_version_id': job['resource_version_id']},
                           order=('-created_at', '-id'))
        status = processing_state_from_stages(job, stages)
        if status == 'CONTENT_READY':
            units = repo.find('content_units', {'resource_version_id': job['resource_version_id']},
                              order=('sequence_no', 'id'), limit=10001)
            generation = sum(stage['stage'] == 'MONGO_PROJECTION' for stage in stages)
            if len(units) > 10000 or set(state['units']) != {unit['id'] for unit in units}:
                return 'FINALIZATION_PENDING'
            try:
                documents = None
                if content_reader is None:
                    batch_reader = getattr(self.pipeline.mongo, 'units', None)
                    content_reader = batch_reader if callable(batch_reader) else None
                if content_reader is not None:
                    documents, expected = {}, {unit['id'] for unit in units}
                    for offset in range(0, len(units) + 1, 100):
                        batch = content_reader(job['resource_version_id'], limit=100, offset=offset)
                        for document in batch:
                            if document.id not in expected or document.id in documents:
                                raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
                            documents[document.id] = document
                        if len(batch) < 100:
                            break
                for unit in units:
                    document = (documents.get(unit['id']) if documents is not None else
                                self.pipeline.mongo.get_unit(unit['id'], version_id=unit['resource_version_id']))
                    if (document is None or document.projection_generation != generation
                            or document.page_from != unit['page_from'] or document.page_to != unit['page_to']):
                        return 'FINALIZATION_PENDING'
                    self._verified_unit(unit, state, document)
            except DomainError as exc:
                return 'FINALIZATION_PENDING' if str(exc) == 'SOURCE_MONGO_UNAVAILABLE' else 'FINALIZATION_FAILED'
        return status

    def unit_texts(self, repo, unit_ids, documents=None, *, units=None):
        result, states = {}, {}
        for unit_id in unit_ids:
            unit = units.get(unit_id) if units is not None else repo.get('content_units', unit_id)
            version_id = unit['resource_version_id'] if unit else None
            if version_id not in states:
                states[version_id] = self.pipeline.state(version_id, repo) if version_id else None
            state = states[version_id]
            if not state:
                if source_owned_id(unit_id, 'SL'):
                    raise DomainError('SOURCE_OCR_PROVENANCE_REQUIRED')
                result[unit_id] = self.legacy.unit_text(repo, unit_id)
                continue
            doc = documents.get(unit_id) if documents is not None else None
            result[unit_id] = self._verified_unit(unit, state, doc)
        return result

    def page_by_task(self, repo, task_id):
        task = repo.get('ocr_page_tasks', task_id)
        job = repo.get('ocr_jobs', task['job_id']) if task else None
        state = self.pipeline.state(job['resource_version_id'], repo) if job else None
        if state is None:
            if job and source_owned_id(job['id'], 'SJ'):
                raise DomainError('SOURCE_OCR_PROVENANCE_REQUIRED')
            return self.legacy.page_by_task(repo, task_id)
        if job['id'] != stable_id('SJ', state['identity']['namespace'], state['source_job_id']):
            raise DomainError('SOURCE_OCR_JOB_CONFLICT')
        if not self.pipeline.authorize_identity(state['identity']):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        document = self.pipeline.verified_page(state, task['page_num'])
        if document is None:
            return None
        proof = state['pages'][str(task['page_num'])]
        metadata = dict(proof.get('result_metadata') or {})
        if 'paired_segments' in metadata:
            segments = []
            for segment in metadata['paired_segments']:
                original = document.raw_text[segment['raw_start']:segment['raw_end']]
                value = {key: content for key, content in segment.items() if key != '_translation_reuses_original'}
                value['original_text'] = original
                if segment.get('_translation_reuses_original'):
                    value['translated_text'] = original
                segments.append(value)
            metadata['paired_segments'] = segments
        return dict(id=stable_id('SR', state['source_job_id'], task['page_num']), page_task_id=task_id,
                    created_at=document.created_at, updated_at=document.updated_at,
                    raw_text=document.raw_text, normalized_text=normalize_text(document.raw_text),
                    engine_name=document.engine_name, engine_version=document.engine_version,
                    confidence=document.confidence, result_metadata=metadata)

    def insert_page(self, repo, values):
        raise DomainError('SOURCE_OCR_MASTER_RAW_WRITE_FORBIDDEN')

    def set_normalized(self, repo, result_id, text, updated_at):
        if result_id.startswith('SR'):
            return  # Normalization is materialized in Mongo units, never MASTER raw.
        self.legacy.set_normalized(repo, result_id, text, updated_at)

    def set_metadata(self, repo, result_id, metadata):
        if not result_id.startswith('SR'):
            return self.legacy.set_metadata(repo, result_id, metadata)
        for version_id, _ in self.pipeline.states():
            state = self.pipeline.state(version_id, repo)
            if not state:
                continue
            for number, proof in state['pages'].items():
                if stable_id('SR', state['source_job_id'], int(number)) == result_id:
                    state['pages'][number] = dict(proof, result_metadata=metadata_without_raw(metadata))
                    self.pipeline._save(repo, version_id, state)
                    return
        raise DomainError('SOURCE_OCR_PAGE_NOT_FOUND')

    def unit_text(self, repo, unit_id):
        unit = repo.get('content_units', unit_id)
        state = self.pipeline.state(unit['resource_version_id'], repo) if unit else None
        if state is None:
            if source_owned_id(unit_id, 'SL'):
                raise DomainError('SOURCE_OCR_PROVENANCE_REQUIRED')
            return self.legacy.unit_text(repo, unit_id)
        return self._verified_unit(unit, state)

    def _verified_unit(self, unit, state, document=None):
        unit_id = unit['id']
        proof = state.get('units', {}).get(unit_id)
        if proof is None:
            raise DomainError('SOURCE_OCR_UNIT_NOT_READY')
        document = document or self.pipeline.mongo.get_unit(unit_id, version_id=unit['resource_version_id'])
        if document is None:
            return None
        if (document.content_unit_id != unit_id
                or sha256(document.text.encode()).hexdigest() != proof['text_sha256']
                or document.page_result_id != proof['page_result_id']
                or document.resource_version_id != unit['resource_version_id'] or not document.is_current):
            raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
        return {'text': document.text, 'page_result_id': proof['page_result_id'], 'content_unit_id': unit_id}

    def insert_unit_text(self, repo, unit_id, result_id, text):
        unit = repo.get('content_units', unit_id)
        state = self.pipeline.state(unit['resource_version_id'], repo)
        if state is None:
            if source_owned_id(unit_id, 'SL'):
                raise DomainError('SOURCE_OCR_PROVENANCE_REQUIRED')
            return self.legacy.insert_unit_text(repo, unit_id, result_id, text)
        checksum = sha256(text.encode()).hexdigest()
        existing = state['units'].get(unit_id)
        if existing and (existing['text_sha256'] != checksum or existing['page_result_id'] != result_id):
            raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
        prior = self.pipeline.mongo.get_unit(unit_id, version_id=unit['resource_version_id'])
        if prior and (sha256(prior.text.encode()).hexdigest() != checksum or prior.page_result_id != result_id):
            raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
        generation = max(1, repo.count('etl_stage_runs', {
            'resource_version_id': unit['resource_version_id'], 'stage': 'MONGO_PROJECTION'}))
        stamp = now()
        self.pipeline.container.documents.upsert_content_unit(dict(
            _id=unit_id, content_unit_id=unit_id, resource_version_id=unit['resource_version_id'],
            page_result_id=result_id, title=unit['title'], page_from=unit['page_from'], page_to=unit['page_to'],
            text=text, markdown=text, keywords=[], is_current=True, projection_generation=generation,
            created_at=stamp, updated_at=stamp))
        doc = self.pipeline.mongo.get_unit(unit_id, version_id=unit['resource_version_id'])
        if doc is None or doc.text != text or doc.projection_generation != generation:
            raise DomainError('SOURCE_OCR_UNIT_READBACK_FAILED')
        proof = dict(text_sha256=checksum, page_result_id=result_id)
        if unit['unit_type'] in ('PART', 'CHAPTER', 'LESSON', 'SECTION', 'SUBSECTION'):
            proof['source_unit_id'] = self.pipeline.ensure_structural_unit(state, unit)
        state['units'][unit_id] = proof
        self.pipeline._save(repo, unit['resource_version_id'], state)
        repo.update('content_units', unit_id, {'mongo_id': unit_id})

    def update_unit_text(self, repo, unit_id, result_id, text):
        return self.insert_unit_text(repo, unit_id, result_id, text)

    def delete_unit_text(self, repo, unit_id):
        unit = repo.get('content_units', unit_id)
        if source_owned_id(unit_id, 'SL') or (unit and self.pipeline.state(unit['resource_version_id'], repo)):
            raise DomainError('SOURCE_OCR_IMMUTABLE_UNIT_SET_CHANGED')
        return self.legacy.delete_unit_text(repo, unit_id)


class SourceOnlyProjector:
    def __init__(self, pipeline, legacy):
        self.pipeline, self.legacy = pipeline, legacy

    def project(self, version_id, job_id, stage_id=None, generation=None):
        state = self.pipeline.state(version_id)
        if state is None:
            if source_owned_id(job_id, 'SJ'):
                raise DomainError('SOURCE_OCR_PROVENANCE_REQUIRED')
            return self.legacy.project(version_id, job_id, stage_id, generation)
        if stage_id is None or type(generation) is not int or generation < 1:
            raise DomainError('SOURCE_OCR_PROJECTION_CHECKPOINT_REQUIRED')
        if job_id != stable_id('SJ', state['identity']['namespace'], state['source_job_id']):
            raise DomainError('SOURCE_OCR_JOB_CONFLICT')
        snapshot = self.pipeline.snapshot(state)
        proof = self.pipeline.probe.probe(snapshot, generation=state['generation'],
                                          processing_fingerprint=state['fingerprint'])
        if proof['status'] != 'COMPLETE_VERIFIED':
            raise DomainError('SOURCE_OCR_NOT_READY')
        if {unit['id'] for unit in snapshot['units']} != {
                proof['source_unit_id'] for proof in state['units'].values() if proof.get('source_unit_id')}:
            raise DomainError('SOURCE_OCR_STRUCTURAL_MAPPING_REQUIRED')
        with self.pipeline.uow() as repo:
            stage = repo.get('etl_stage_runs', stage_id)
            if not stage or stage['status'] != 'RUNNING' or stage['resource_version_id'] != version_id:
                raise DomainError('SOURCE_OCR_PROJECTION_OWNERSHIP_LOST')
            units = repo.find('content_units', {'resource_version_id': version_id}, order=('sequence_no', 'id'))
            if set(state['units']) != {unit['id'] for unit in units}:
                raise DomainError('SOURCE_OCR_UNIT_SET_CONFLICT')
        if any(self.pipeline.mongo.get_unit(unit['id'], version_id=version_id) is None for unit in units):
            self.pipeline.container.postprocess.run_locked(version_id)
        with self.pipeline.uow() as repo:
            for unit in units:
                body = self.pipeline.container.ocr_text_store.unit_text(repo, unit['id'])
                # Only normalized units change projection generation on a real retry.
                stamp = now()
                self.pipeline.container.documents.upsert_content_unit(dict(
                    _id=unit['id'], content_unit_id=unit['id'], resource_version_id=version_id,
                    page_result_id=body['page_result_id'], title=unit['title'], page_from=unit['page_from'],
                    page_to=unit['page_to'], text=body['text'], markdown=body['text'], keywords=[],
                    is_current=True, projection_generation=generation, created_at=stamp, updated_at=stamp))
                doc = self.pipeline.mongo.get_unit(unit['id'], version_id=version_id)
                if doc is None or doc.projection_generation != generation or doc.text != body['text']:
                    raise DomainError('SOURCE_OCR_UNIT_READBACK_FAILED')
        if not self.pipeline.authorize_identity(state['identity']):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        self.pipeline.cleanup_staging(state)
        return {'ocr_pages': snapshot['job']['total_pages'], 'content_units': len(units)}

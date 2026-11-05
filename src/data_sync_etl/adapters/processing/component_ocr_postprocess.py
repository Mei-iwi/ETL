"""Postprocess verified component pages without writing OCR text to Master."""
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data_sync_etl.adapters.persistence.component_ocr_outbox import (
    complete_verified_unit,
    component_tables,
    require_isolated_prototype,
)
from data_sync_etl.adapters.persistence.component_ocr_units import (
    component_postprocess_manifests,
    component_unit_provenance,
)
from data_sync_etl.application.postprocess import Postprocessor
from data_sync_etl.domain.core import DomainError, normalize_text


def _stable_unit_id(namespace, version_id, generation, sequence):
    key = json.dumps([namespace, version_id, generation, sequence], ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(key.encode('ascii')).hexdigest()


def _insert_immutable(conn, table, values, keys):
    if conn.dialect.name not in ('postgresql', 'sqlite'):
        raise DomainError('COMPONENT_DATABASE_UNSUPPORTED')
    insert = pg_insert if conn.dialect.name == 'postgresql' else sqlite_insert
    statement = insert(table).values(**values).on_conflict_do_nothing(index_elements=[table.c[key] for key in keys])
    conn.execute(statement)
    saved = conn.execute(select(table).where(*[table.c[key] == values[key] for key in keys])).mappings().one()
    if any(saved[name] != value for name, value in values.items()):
        raise DomainError('COMPONENT_POSTPROCESS_CONFLICT')


class ComponentOCRPostprocessor:
    def __init__(self, engine, content, *, verify_mapping, normalizer=normalize_text):
        require_isolated_prototype(engine)
        self.engine, self.content, self.verify_mapping, self.normalizer = engine, content, verify_mapping, normalizer

    def run_job(self, job_id):
        jobs = component_tables['job']
        with self.engine.begin() as conn:
            found = conn.execute(select(jobs).where(jobs.c.source_job_id == job_id).limit(2)).mappings().all()
        if len(found) != 1:
            raise DomainError('COMPONENT_JOB_IDENTITY_MISMATCH')
        return self.run(found[0]['namespace'], job_id, found[0]['generation'])

    def run(self, namespace, job_id, generation):
        jobs, pages, units = component_tables['job'], component_tables['page'], component_tables['unit']
        with self.engine.begin() as conn:
            job = conn.execute(select(jobs).where(jobs.c.namespace == namespace, jobs.c.source_job_id == job_id,
                                                  jobs.c.generation == generation).with_for_update()).mappings().one_or_none()
            if (not job or job['tombstone'] or job['status'] != 'COMPLETED' or job['failed_pages']
                    or job['total_pages'] < 1 or job['completed_pages'] != job['total_pages']):
                raise DomainError('COMPONENT_OCR_NOT_READY')
            if not self.verify_mapping('job', dict(job)):
                raise DomainError('COMPONENT_CONTENT_UNAUTHORIZED')
            tasks = conn.execute(select(pages).where(
                pages.c.namespace == namespace, pages.c.source_job_id == job_id,
                pages.c.generation == generation, pages.c.tombstone.is_(False),
            ).order_by(pages.c.page_num)).mappings().all()
            if (len(tasks) != job['total_pages']
                    or [task['page_num'] for task in tasks] != list(range(1, job['total_pages'] + 1))
                    or any(task['status'] != 'SUCCEEDED' or not task['verified'] for task in tasks)):
                raise DomainError('COMPONENT_OCR_NOT_READY')
            digest, count, outline_parents = hashlib.sha256(b'page-text-nfc-v1'), 0, {}
            for task in tasks:
                page_num = task['page_num']
                result = self.content.get_page(namespace=namespace, version_id=job['source_version_id'],
                                               generation=generation, page_num=page_num,
                                               source_hash=job['source_hash'], fingerprint=job['processing_fingerprint'])
                if result is None:
                    raise DomainError('COMPONENT_OCR_NOT_READY')
                digest.update(f"{page_num}:{task['status']}:".encode())
                normalized = self.normalizer(result['raw_text'])
                digest.update(hashlib.sha256(normalized.encode()).digest())
                for logical_index, (text, page, title, unit_type, heading) in enumerate(
                        Postprocessor._text_units(result, page_num)):
                    text = self.normalizer(text)
                    if not text:
                        continue
                    count += 1
                    if heading is not None:
                        outline_parents = {level: key for level, key in outline_parents.items() if level < heading}
                    parent = outline_parents[max(outline_parents)] if outline_parents else None
                    metadata = result['result_metadata'] or {}
                    logical = metadata.get('logical_units', [])
                    locator = logical[logical_index].get('locator', {}) if logical else None
                    if locator:
                        locator = {key: value for key, value in locator.items() if key != 'frame'}
                        paired = [segment for segment in metadata.get('paired_segments', [])
                                  if logical[logical_index]['start'] <= segment['raw_start'] < logical[logical_index]['end']]
                        locator['segment_ids'] = [segment['segment_id'] for segment in paired]
                    if metadata.get('source_type') == 'media' and locator:
                        unit_type = 'TXT_TEXT'
                    unit_id = _stable_unit_id(namespace, job['source_version_id'], generation, count)
                    identity = dict(namespace=namespace, version_id=job['source_version_id'], generation=generation,
                                    unit_id=unit_id, source_hash=job['source_hash'],
                                    fingerprint=job['processing_fingerprint'])
                    self.content.put_unit(**identity, text=text)
                    unit_doc = self.content.get_unit(**identity)
                    if unit_doc is None:
                        raise DomainError('COMPONENT_CONTENT_UNAVAILABLE')
                    snapshot = dict(namespace=namespace, source_unit_id=unit_id, generation=generation,
                                    source_version_id=job['source_version_id'], master_version_id=job['master_version_id'],
                                    master_resource_id=job['master_resource_id'], parent_unit_id=parent,
                                    unit_type=unit_type, sequence_no=count, page_from=page, page_to=page,
                                    review_status='UNREVIEWED', retrieval_eligible=False,
                                    text_checksum=unit_doc['checksum'], verified=True, revision=1, tombstone=False)
                    complete_verified_unit(conn, self.content, snapshot, source_hash=job['source_hash'],
                                           fingerprint=job['processing_fingerprint'])
                    stored = conn.execute(select(units).where(units.c.namespace == namespace,
                        units.c.source_unit_id == unit_id, units.c.generation == generation)).mappings().one()
                    for key in ('source_version_id', 'master_version_id', 'master_resource_id', 'parent_unit_id',
                                'unit_type', 'sequence_no', 'page_from', 'page_to', 'text_checksum'):
                        if stored[key] != snapshot[key]:
                            raise DomainError('COMPONENT_POSTPROCESS_CONFLICT')
                    provenance = dict(namespace=namespace, source_unit_id=unit_id, generation=generation,
                                      source_version_id=job['source_version_id'], source_task_id=task['source_task_id'],
                                      page_num=page_num, title=title, source_locator=locator)
                    _insert_immutable(conn, component_unit_provenance, provenance,
                                      ('namespace', 'source_unit_id', 'generation'))
                    if heading is not None:
                        outline_parents[heading] = unit_id
            if not count:
                raise DomainError('EMPTY_EXTRACTED_CONTENT')
            existing = conn.execute(select(units.c.source_unit_id).where(
                units.c.namespace == namespace, units.c.source_version_id == job['source_version_id'],
                units.c.generation == generation, units.c.tombstone.is_(False))).all()
            if len(existing) != count or not self.verify_mapping('job', dict(job)):
                raise DomainError('COMPONENT_POSTPROCESS_CONFLICT')
            manifest = dict(namespace=namespace, source_version_id=job['source_version_id'], generation=generation,
                            source_job_id=job_id, source_hash=job['source_hash'],
                            processing_fingerprint=job['processing_fingerprint'], content_fingerprint=digest.hexdigest(),
                            unit_count=count)
            _insert_immutable(conn, component_postprocess_manifests, manifest,
                              ('namespace', 'source_version_id', 'generation'))
        return {'processing_status': 'POSTPROCESSED', 'content_unit_count': count,
                'input_fingerprint': manifest['content_fingerprint']}

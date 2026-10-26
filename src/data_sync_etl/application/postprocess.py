import hashlib

from data_sync_etl.domain.core import DomainError, normalize_text, now
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.repositories import UnitOfWork


class Postprocessor:
    def __init__(self, uow: UnitOfWork, normalizer=normalize_text):
        self.uow, self.normalizer = uow, normalizer

    @staticmethod
    def _text_units(result, page):
        metadata = result['result_metadata'] or {}
        if 'logical_units' not in metadata:
            yield result['raw_text'], page, None, 'PAGE_TEXT', None
        else:
            logical = metadata['logical_units']
            # Reject uncertain/malformed outlines as flat content; never invent hierarchy.
            levels = [u.get('locator', {}).get('heading_level') for u in logical]
            structured = (metadata.get('source_type') == 'docx'
                          and metadata.get('structure_version') == 'docx-outline-v1'
                          and all(level is None or type(level) is int and 1 <= level <= 3 for level in levels)
                          and all(u.get('locator', {}).get('char_offset', 0) == 0
                                  for u, level in zip(logical, levels, strict=True) if level is not None))
            for unit, level in zip(logical, levels, strict=True):
                heading = level if structured else None
                yield (result['raw_text'][unit['start']:unit['end']], None,
                       result['raw_text'][unit['start']:unit['end']][:500] if heading else unit['title'],
                       {1: 'CHAPTER', 2: 'LESSON', 3: 'SECTION'}[heading] if heading else metadata['source_type'].upper() + '_TEXT',
                       heading)

    @staticmethod
    def _save_unit(repo, version_id, sequence, page, title, unit_type, result, text, parent_id=None):
        values = dict(page_from=page, page_to=page, title=title, unit_type=unit_type,
                      parent_unit_id=parent_id)
        units = repo.find('content_units', {'resource_version_id': version_id, 'sequence_no': sequence}, limit=1)
        if units:
            unit = units[0]
            body = repo.get('content_unit_texts', unit['id'])
            if (any(unit[key] != values[key] for key in values)
                    or body['text'] != text or body['page_result_id'] != result['id']):
                repo.update('content_units', unit['id'], {**values, 'review_status': 'UNREVIEWED',
                                                        'retrieval_eligible': False, 'updated_at': now()})
                repo.update('content_unit_texts', unit['id'], dict(text=text, page_result_id=result['id']))
        else:
            unit = repo.insert('content_units', {
                **values, 'resource_version_id': version_id, 'sequence_no': sequence,
                'review_status': 'UNREVIEWED', 'retrieval_eligible': False,
            })
            repo.insert('content_unit_texts', dict(content_unit_id=unit['id'], page_result_id=result['id'], text=text))
        return unit['id']

    def run(self, version_id, *, allow_partial=False):
        with self.uow.processing_lock(version_id) as acquired:
            if not acquired:
                raise DomainError("Postprocess already running for this resource version")
            return self.run_locked(version_id, allow_partial=allow_partial)

    def run_locked(self, version_id, *, allow_partial=False):
        """Run only while the caller holds the version's processing lock."""
        with flow():
            with self.uow() as repo:
                version = repo.get("resource_versions", version_id)
                if not version:
                    raise DomainError("Resource version not found")
                from data_sync_etl.domain.resource_deletion import resource_deleted
                resource = repo.get('master_learning_resources', version['resource_id'], lock=True)
                if not resource or resource_deleted(resource):
                    raise DomainError('RESOURCE_NOT_AVAILABLE')
                stage = repo.insert(
                    "etl_stage_runs",
                    dict(
                        resource_version_id=version_id,
                        stage="POSTPROCESS",
                        status="RUNNING",
                        started_at=now(),
                        created_at=now(),
                    ),
                )
            try:
                with self.uow() as repo:
                    repo.get("resource_versions", version_id, lock=True)
                    jobs = repo.find(
                        "ocr_jobs",
                        {"resource_version_id": version_id},
                        order=("-created_at", "-id"),
                        limit=1,
                    )
                    if not jobs or jobs[0]["status"] not in ("COMPLETED", "COMPLETED_WITH_ERRORS"):
                        raise DomainError("Postprocess requires a completed OCR job")
                    job = jobs[0]
                    if not allow_partial and (
                        job["status"] != "COMPLETED"
                        or job["failed_pages"]
                        or job["completed_pages"] != job["total_pages"]
                    ):
                        raise DomainError("Postprocess requires all OCR pages to succeed")
                    digest = hashlib.sha256(b"page-text-nfc-v1")
                    count, successful = 0, 0
                    outline_parents = {}
                    # One page/result at a time, keeping large document text out of a whole-file buffer.
                    for page in range(1, job["total_pages"] + 1):
                        tasks = repo.find(
                            "ocr_page_tasks", {"job_id": job["id"], "page_num": page}, limit=1
                        )
                        if not tasks:
                            raise DomainError("OCR page task is missing")
                        task = tasks[0]
                        digest.update(f"{page}:{task['status']}:".encode())
                        if task["status"] != "SUCCEEDED":
                            if not allow_partial:
                                raise DomainError("OCR page has not succeeded")
                            continue
                        successful += 1
                        results = repo.find(
                            "ocr_page_results", {"page_task_id": task["id"]}, limit=1
                        )
                        if not results:
                            raise DomainError("OCR page result is missing")
                        result = results[0]
                        normalized = self.normalizer(result["raw_text"])
                        digest.update(hashlib.sha256(normalized.encode()).digest())
                        if result["normalized_text"] != normalized:
                            repo.update(
                                "ocr_page_results",
                                result["id"],
                                {"normalized_text": normalized, "updated_at": now()},
                            )
                        for text, source_page, title, unit_type, heading in self._text_units(result, page):
                            text = self.normalizer(text)
                            if not text:
                                continue
                            count += 1
                            if heading is not None:
                                outline_parents = {level: key for level, key in outline_parents.items() if level < heading}
                            parent = outline_parents[max(outline_parents)] if outline_parents else None
                            unit_id = self._save_unit(repo, version_id, count, source_page, title, unit_type, result, text, parent)
                            if heading is not None:
                                outline_parents[heading] = unit_id
                    if count == 0:
                        raise DomainError('EMPTY_EXTRACTED_CONTENT')
                    # Remove obsolete trailing baseline units without materializing document text.
                    # Detach obsolete edges before deleting trailing units (FK RESTRICT).
                    obsolete = repo.find('content_units', {'resource_version_id': version_id})
                    for unit in obsolete:
                        if unit['sequence_no'] > count and unit['parent_unit_id'] is not None:
                            repo.update('content_units', unit['id'], {'parent_unit_id': None})
                    sequence = count + 1
                    while units := repo.find(
                        "content_units",
                        {"resource_version_id": version_id, "sequence_no": sequence},
                        limit=1,
                    ):
                        repo.delete("content_unit_texts", {"content_unit_id": units[0]["id"]})
                        repo.delete("content_units", {"id": units[0]["id"]})
                        sequence += 1
                    fingerprint = digest.hexdigest()
                    # No postprocess queue exists. Leave the legacy enqueue timestamp
                    # untouched; etl_stage_runs records actual start/finish/status.
                    repo.update(
                        "etl_stage_runs",
                        stage["id"],
                        dict(status="COMPLETED", finished_at=now(), input_fingerprint=fingerprint),
                    )
                coverage = {
                    "successful_pages": successful,
                    "total_pages": job["total_pages"],
                    "failed_pages": job["failed_pages"],
                }
                event(
                    resource_version_id=version_id,
                    job_id=job["id"],
                    stage="postprocess",
                    action="PARTIAL_COVERAGE" if job["failed_pages"] else "COMPLETED",
                    coverage=coverage,
                )
                return {
                    "processing_status": "PARTIAL_CONTENT"
                    if job["failed_pages"]
                    else "POSTPROCESSED",
                    "content_unit_count": count,
                    "coverage": coverage,
                    "input_fingerprint": fingerprint,
                }
            except Exception:
                with self.uow() as repo:
                    repo.update(
                        "etl_stage_runs",
                        stage["id"],
                        dict(
                            status="FAILED",
                            finished_at=now(),
                            error_message="Postprocess failed; existing content preserved",
                        ),
                    )
                raise DomainError("Postprocess failed; existing content preserved") from None

from hashlib import sha256

from data_sync_etl.adapters.persistence.legacy_ocr_text import LegacyOCRText


def projection_fingerprint(job_id: str, postprocess: dict) -> str:
    return sha256(f"{job_id}:{postprocess['input_fingerprint']}".encode()).hexdigest()


def intake_status(version, job=None, stages=()):
    """Operational progress; does not change lifecycle/ACL or database enums."""
    options = version.get('processing_options') or {}
    audit = options.get('_source_ocr_audit') or {}
    if job:
        if job['status'] == 'COMPLETED' and not options.get('_source_ocr'):
            legacy_gate = {'UNVERIFIED':'PROVENANCE_REQUIRED', 'INVALID_OR_REVOKED':'SOURCE_INVALID',
                           'WAITING_FOR_PROCESS_AUTHORIZATION':'WAITING_AUTHORIZATION',
                           'WAITING_FOR_SOURCE_MAPPING':'WAITING_SOURCE_MAPPING',
                           'RETRY_EXHAUSTED':'PROCESSING_FAILED', 'RECHECK_REQUIRED':'RETRY_WAIT'}
            if audit.get('status') in legacy_gate:
                return legacy_gate[audit['status']]
        if job['status'] == 'PENDING':
            return 'OCR_QUEUED'
        if job['status'] == 'RUNNING':
            return 'OCR_RUNNING'
        state = processing_state_from_stages(job, stages)
        if state == 'CONTENT_READY':
            index = next((s for s in stages if s['stage'] == 'INDEX_BUILD'), None)
            if not index:
                return 'CONTENT_READY'
            if index and index['status'] == 'COMPLETED':
                return 'READY'
            return 'INDEX_FAILED' if index and index['status'] == 'FAILED' else 'INDEXING'
        return state
    status = audit.get('status')
    if status in ('ABSENT', 'PARTIAL') and audit.get('execution_authorized'):
        return 'OCR_QUEUED'
    return {'CHECKING': 'CHECKING_OCR', 'RECHECK_REQUIRED': 'RETRY_WAIT', 'QUEUED': 'OCR_QUEUED',
            'WAITING_FOR_PROCESS_AUTHORIZATION': 'WAITING_AUTHORIZATION',
            'WAITING_FOR_SOURCE_MAPPING': 'WAITING_SOURCE_MAPPING',
            'UNVERIFIED': 'PROVENANCE_REQUIRED', 'INVALID_OR_REVOKED': 'SOURCE_INVALID',
            'RETRYABLE_ERROR': 'RETRY_WAIT', 'RETRY_EXHAUSTED': 'PROCESSING_FAILED',
            'COMPLETE_VERIFIED': 'FINALIZATION_PENDING'}.get(status,
                'RECEIVED' if options.get('source_version_identity') or version.get('source_version_id') else 'NOT_STARTED')


def processing_state(repo, job: dict, text_store=None, *, content_reader=None) -> str:
    phase_reader = getattr(text_store, 'processing_state_from_content', None)
    if content_reader is not None and callable(phase_reader):
        return phase_reader(repo, job, content_reader)
    if text_store is not None and hasattr(text_store, 'processing_state'):
        return text_store.processing_state(repo, job)
    if job['status'] == 'COMPLETED':
        tasks = repo.find('ocr_page_tasks', {'job_id': job['id']}, order=('page_num', 'id'), limit=1)
        if tasks:
            result = (text_store or LegacyOCRText()).page_by_task(repo, tasks[0]['id'])
            if result and (result['result_metadata'] or {}).get('extraction_status') == 'no_extractable_content':
                return 'NO_EXTRACTABLE_CONTENT'
    stages = repo.find(
        "etl_stage_runs",
        {"resource_version_id": job["resource_version_id"]},
        order=("-created_at", "-id"),
    ) if job['status'] == 'COMPLETED' else []
    return processing_state_from_stages(job, stages)


def processing_state_from_stages(job: dict, stages: list[dict]) -> str:
    if job["status"] in ("FAILED", "COMPLETED_WITH_ERRORS"):
        return "OCR_FAILED"
    if job["status"] != "COMPLETED":
        return "OCR_PENDING"
    if job["failed_pages"] or job["completed_pages"] != job["total_pages"]:
        return "OCR_FAILED"
    post = next((s for s in stages if s["stage"] == "POSTPROCESS"), None)
    projection = next((s for s in stages if s["stage"] == "MONGO_PROJECTION"), None)
    retry = next((s for s in stages if s["stage"] == "FINALIZATION_RETRY"), None)
    if projection:
        if (
            projection["status"] == "COMPLETED"
            and post
            and post["status"] == "COMPLETED"
            and projection["input_fingerprint"] == projection_fingerprint(job["id"], post)
        ):
            return "CONTENT_READY"
        if projection["status"] == "FAILED":
            if retry and retry["status"] == "PENDING":
                return "FINALIZATION_PENDING"
            return "FINALIZATION_FAILED"
        if projection["status"] == "RUNNING":
            return "FINALIZING"
    if retry and retry["status"] == "PENDING":
        return "FINALIZATION_PENDING"
    if post and post["status"] == "FAILED":
        return "FINALIZATION_FAILED"
    return "FINALIZATION_PENDING"

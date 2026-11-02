from hashlib import sha256

from data_sync_etl.adapters.persistence.legacy_ocr_text import LegacyOCRText


def projection_fingerprint(job_id: str, postprocess: dict) -> str:
    return sha256(f"{job_id}:{postprocess['input_fingerprint']}".encode()).hexdigest()


def processing_state(repo, job: dict, text_store=None) -> str:
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

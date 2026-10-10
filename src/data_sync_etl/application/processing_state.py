from hashlib import sha256


def projection_fingerprint(job_id: str, postprocess: dict) -> str:
    return sha256(f"{job_id}:{postprocess['input_fingerprint']}".encode()).hexdigest()


def processing_state(repo, job: dict) -> str:
    if job["status"] in ("FAILED", "COMPLETED_WITH_ERRORS"):
        return "OCR_FAILED"
    if job["status"] != "COMPLETED":
        return "OCR_PENDING"
    if job["failed_pages"] or job["completed_pages"] != job["total_pages"]:
        return "OCR_FAILED"
    stages = repo.find(
        "etl_stage_runs",
        {"resource_version_id": job["resource_version_id"]},
        order=("-created_at", "-id"),
    )
    post = next((s for s in stages if s["stage"] == "POSTPROCESS"), None)
    projection = next((s for s in stages if s["stage"] == "MONGO_PROJECTION"), None)
    if projection:
        if (
            projection["status"] == "COMPLETED"
            and post
            and post["status"] == "COMPLETED"
            and projection["input_fingerprint"] == projection_fingerprint(job["id"], post)
        ):
            return "CONTENT_READY"
        if projection["status"] == "FAILED":
            return "FINALIZATION_FAILED"
        if projection["status"] == "RUNNING":
            return "FINALIZING"
    return "FINALIZATION_PENDING"

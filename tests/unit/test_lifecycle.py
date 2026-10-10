from datetime import timedelta

import pytest
from conftest import completed

from data_sync_etl.domain.core import DomainError, now


def test_terminal_job_is_reused_and_no_enqueue_is_claimed(container):
    version, job = completed(container)
    assert container.ocr.create_job(version["id"])["id"] == job["id"]
    container.postprocess.run(version["id"])
    with container.uow() as repo:
        assert repo.get("ocr_jobs", job["id"])["postprocess_enqueued_at"] is None
        stage = repo.find(
            "etl_stage_runs",
            {"resource_version_id": version["id"], "stage": "POSTPROCESS"},
        )[0]
        assert stage["status"] == "COMPLETED" and stage["finished_at"] is not None


def test_legacy_enqueue_value_is_preserved(container):
    version, job = completed(container)
    legacy = now() - timedelta(days=1)
    with container.uow() as repo:
        repo.update("ocr_jobs", job["id"], {"postprocess_enqueued_at": legacy})
    container.postprocess.run(version["id"])
    with container.uow() as repo:
        assert repo.get("ocr_jobs", job["id"])["postprocess_enqueued_at"] == legacy


def test_latest_job_must_be_terminal_no_fallback_to_old_output(container):
    version, _ = completed(container)
    container.postprocess.run(version["id"])
    with container.uow() as repo:
        original = repo.find("content_units", {}, order=("sequence_no",))
        newer = repo.insert(
            "ocr_jobs",
            dict(
                resource_version_id=version["id"],
                status="PENDING",
                total_pages=1,
                completed_pages=0,
                failed_pages=0,
                created_at=now() + timedelta(seconds=1),
            ),
        )
    assert container.ocr.create_job(version["id"])["id"] == newer["id"]
    with pytest.raises(DomainError):
        container.postprocess.run(version["id"])
    with container.uow() as repo:
        assert repo.find("content_units", {}, order=("sequence_no",)) == original

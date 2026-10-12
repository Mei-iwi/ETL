from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class ProcessVersionResponse(BaseModel):
    resource_version_id: str
    job_id: str

    job_status: Literal[
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "COMPLETED_WITH_ERRORS",
        "FAILED",
    ]

    action: str
    processing_status: Literal[
        "OCR_PENDING",
        "OCR_FAILED",
        "FINALIZATION_PENDING",
        "FINALIZING",
        "FINALIZATION_FAILED",
        "CONTENT_READY",
    ]
    status_url: str


class RetryProcessingJobResponse(ProcessVersionResponse):
    """Same durable job/status fields returned by the process endpoint."""


class ProcessingJobStatusResponse(BaseModel):
    id: str
    resource_version_id: str
    status: Literal[
        "PENDING",
        "RUNNING",
        "COMPLETED",
        "COMPLETED_WITH_ERRORS",
        "FAILED",
    ]
    processing_status: Literal[
        "OCR_PENDING",
        "OCR_FAILED",
        "FINALIZATION_PENDING",
        "FINALIZING",
        "FINALIZATION_FAILED",
        "CONTENT_READY",
    ]
    total_pages: int
    completed_pages: int
    failed_pages: int
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    stage_runs: list[dict[str, Any]]

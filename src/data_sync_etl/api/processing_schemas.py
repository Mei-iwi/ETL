from typing import Literal

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

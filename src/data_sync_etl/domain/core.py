import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum

from ulid import ULID


class VersionStatus(StrEnum):
    INGESTED = "INGESTED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ReviewStatus(StrEnum):
    UNREVIEWED = "UNREVIEWED"


class DomainError(ValueError):
    """Only static, non-sensitive error messages may cross the public boundary."""


class SyncAlreadyRunning(DomainError):
    pass


class AutoSyncUnavailable(DomainError):
    pass


class AutoSyncBaselineRequired(DomainError):
    pass


class UploadTooLargeError(DomainError):
    pass


class ResourceCreateError(DomainError):
    """Safe, structured error raised while creating a learning resource."""

    def __init__(self, phase: str, code: str, message: str, status_code: int = 400):
        self.phase = phase
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ResourceVersionNotFound(DomainError):
    pass


def now():
    return datetime.now(UTC)


def new_id():
    return str(ULID())


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def job_terminal(completed: int, failed: int, total: int):
    if completed + failed != total:
        return JobStatus.RUNNING
    return JobStatus.COMPLETED_WITH_ERRORS if failed else JobStatus.COMPLETED


class ProcessingJobNotFound(DomainError):
    pass


class ProcessingRetryConflict(DomainError):
    pass

class ResourceNotFound(DomainError): 
    pass

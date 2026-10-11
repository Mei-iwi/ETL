from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CreateResourceVersionResponse(BaseModel):
    action: Literal["CREATED", "NO_CHANGE"]
    resource_id: str
    resource_version_id: str
    parent_version_id: str | None
    version_number: int
    lifecycle_status: str
    file_name: str
    file_size: int
    mime_type: str
    source_hash: str
    uploaded_by: str | None
    created_at: datetime

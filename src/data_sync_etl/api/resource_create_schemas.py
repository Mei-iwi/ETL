from datetime import datetime

from pydantic import BaseModel


class CreateResourceResponse(BaseModel):
    id: str
    title: str
    resource_type_code: str
    media_category: str
    mime_type: str
    file_name: str
    file_size: int
    source_hash: str
    initial_version_id: str
    version_number: int
    lifecycle_status: str
    created_at: datetime

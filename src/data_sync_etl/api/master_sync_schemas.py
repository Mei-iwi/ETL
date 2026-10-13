from typing import Literal

from pydantic import BaseModel, ConfigDict

SyncStream = Literal["all", "resources", "users", "grades", "subjects"]


class MasterSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    stream: SyncStream = "all"
    full: bool = False


class MasterSyncRunResult(BaseModel):
    run_id: str
    records_read: int
    inserted: int
    updated: int
    skipped: int
    failed: int


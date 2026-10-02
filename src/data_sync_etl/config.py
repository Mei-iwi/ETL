from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = Field(default="postgresql+psycopg://localhost/etl", repr=False)
    test_database_url: str | None = Field(default=None, repr=False)
    log_level: str = "INFO"
    storage_root: Path = Path("storage")
    source_fixture_root: Path = Path("fixtures/source")
    source_adapter: Literal["json", "production"] = "json"
    ocr_max_retries: int = Field(default=3, ge=1)
    ocr_heartbeat_timeout_seconds: int = Field(default=120, ge=3)
    sync_batch_size: int = Field(default=100, ge=1, le=10000)
    ocr_engine: Literal["native", "fake"] = "native"
    admin_enabled: bool = False

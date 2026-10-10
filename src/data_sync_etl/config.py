from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )
    database_url: str = Field(
        default="postgresql+psycopg://localhost/etl",
        repr=False
    )
    test_database_url: str | None = Field(default=None, repr=False)
    log_level: str = "INFO"
    storage_root: Path = Path("storage")
    source_fixture_root: Path = Path("fixtures/source")
    source_adapter: Literal["json", "production"] = "json"
    ocr_max_retries: int = Field(default=3, ge=1)
    ocr_heartbeat_timeout_seconds: int = Field(default=120, ge=3)
    ocr_recovery_interval_seconds: int = Field(default=30, ge=1)
    ocr_timeout_seconds: int = Field(default=60, ge=1)
    tesseract_path: str = "tesseract"
    tesseract_languages: str = "vie+eng"
    sync_batch_size: int = Field(default=100, ge=1, le=10000)

    chunk_size_words: int = Field(
        default=200, ge=20, le=500
    )

    chunk_overlap_words: int = Field(
        default=40, ge=0, le=100
    )

    vector_dimension: int = Field(
        default=256, ge=16, le = 4096
    )
    ocr_engine: Literal["native", "fake", "tesseract"] = "native"
    admin_enabled: bool = False

    mongo_uri: str = Field(
        default='mongodb://127.0.0.1:27017',
        repr=False,
    )
    mongo_database: str = 'etl_content'
    finalization_max_attempts: int = Field(default=3, ge=1, le=20)
    finalization_retry_seconds: int = Field(default=30, ge=1, le=3600)

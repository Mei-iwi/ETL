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
    source_adapter: Literal["json", "postgresql", "production"] = "json"
    source_database_url: str | None = Field(default=None, repr=False)
    source_migration_database_url: str | None = Field(default=None, repr=False)
    source_replay_seconds: int = Field(default=300, ge=0)
    source_file_storage_root: Path | None = Field(default=None, repr=False)
    source_file_allowed_statuses: tuple[str, ...] = ("APPROVED",)
    source_file_import_max_attempts: int = Field(default=3, ge=1, le=10)
    source_file_import_retry_seconds: int = Field(default=30, ge=1, le=3600)
    source_file_import_lease_seconds: int = Field(default=120, ge=15, le=3600)
    source_file_reconcile_seconds: int = Field(default=300, ge=10, le=86400)
    ocr_max_retries: int = Field(default=3, ge=1)
    ocr_heartbeat_timeout_seconds: int = Field(default=120, ge=3)
    ocr_recovery_interval_seconds: int = Field(default=30, ge=1)
    ocr_timeout_seconds: int = Field(default=60, ge=1)
    tesseract_path: str = "tesseract"
    tesseract_languages: str = "vie+eng"
    sync_batch_size: int = Field(default=100, ge=1, le=10000)
    master_sync_auto_interval_seconds: int = Field(default=300, ge=30, le=86400)
    master_sync_auto_poll_seconds: int = Field(default=5, ge=1, le=60)
    master_sync_auto_worker_stale_seconds: int = Field(default=30, ge=5, le=300)
    master_sync_auto_lease_seconds: int = Field(default=120, ge=30, le=3600)

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
    upload_request_max_bytes: int = Field(default=501 * 1024 * 1024, ge=1024)

    mongo_uri: str = Field(
        default='mongodb://127.0.0.1:27017',
        repr=False,
    )
    mongo_database: str = 'etl_content'
    master_mongo_identity_bootstrap: bool = False
    # Explicit per-stream approval of _id=Master.id. Never infer source.mongo_id.
    master_mongo_identity_ids: dict[Literal['users', 'resources'], list[str]] = Field(
        default_factory=dict, repr=False,
    )
    finalization_max_attempts: int = Field(default=3, ge=1, le=20)
    finalization_retry_seconds: int = Field(default=30, ge=1, le=3600)
    processing_manual_retry_limit: int = Field(default=3, ge=1, le=20)

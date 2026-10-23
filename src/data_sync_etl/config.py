from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
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
    reconcile_max_affected: int = Field(default=100, ge=1, le=1000)
    reconcile_max_ratio: float = Field(default=0.1, gt=0, le=1)
    source_file_storage_root: Path | None = Field(default=None, repr=False)
    # Policy gates: disabling SOURCE access never restores the historical copier.
    source_reference_enabled: bool = False
    source_temporary_files_enabled: bool = False
    source_temp_root: Path | None = Field(default=None, repr=False)
    source_read_timeout_seconds: int = Field(default=120, ge=1, le=600)
    source_temp_max_concurrency: int = Field(default=4, ge=1, le=32)
    source_temp_orphan_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    office_pdf_executable: Path | None = Field(default=None, repr=False)
    office_pdf_timeout_seconds: int = Field(default=90, ge=5, le=180)
    source_drive_enabled: bool = False
    source_drive_buckets: dict[str, str] = Field(default_factory=dict, repr=False)
    source_local_buckets: tuple[str, ...] = ()
    source_drive_auth_type: Literal['service_account', 'authorized_user'] = 'service_account'
    source_drive_credentials_file: Path | None = Field(default=None, repr=False)
    source_drive_timeout_seconds: int = Field(default=15, ge=1, le=60)
    source_drive_total_seconds: int = Field(default=90, ge=5, le=300)
    source_drive_max_attempts: int = Field(default=3, ge=1, le=5)
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
    index_auto_enabled: bool = False
    index_embedding_model_path: Path | None = Field(default=None, repr=False)
    index_embedding_model_id: str = Field(default="", max_length=255)
    index_embedding_model_version: str = Field(default="", max_length=128)
    index_embedding_dimension: int = Field(default=384, ge=1, le=4096)
    index_historical_embedding_dimensions: list[int] = Field(default_factory=list, max_length=8)
    index_max_chunks: int = Field(default=10000, ge=1, le=50000)
    index_max_units: int = Field(default=10000, ge=1, le=50000)
    index_max_source_chars: int = Field(default=10000000, ge=1, le=50000000)
    index_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    # Opt-in parallel index. Legacy bag vectors/BoW remain the default.
    semantic_enabled: bool = False
    semantic_strategy: Literal['raw', 'contextual'] = 'contextual'
    semantic_model_path: Path | None = Field(default=None, repr=False)
    semantic_model_revision: str = Field(default='', max_length=40)
    semantic_device: str = Field(default='cpu', pattern=r'^(cpu|cuda:\d+)$')
    semantic_batch_size: int = Field(default=2, ge=1, le=16)
    semantic_max_tokens: int = Field(default=1024, ge=64, le=8192)
    semantic_max_candidates: int = Field(default=10000, ge=1, le=50000)
    semantic_min_score: float = Field(default=0., ge=0., le=1., allow_inf_nan=False)
    semantic_rerank_enabled: bool = False
    semantic_rerank_top_k: int = Field(default=20, ge=1, le=50)
    search_max_bags: int = Field(default=2000, ge=1, le=10000)
    search_timeout_seconds: int = Field(default=15, ge=1, le=120)
    search_model_warmup_timeout_seconds: int = Field(default=60, ge=1, le=120)
    search_query_embedding_cache_enabled: bool = False
    app_environment: Literal['local', 'development', 'test', 'staging', 'production'] | None = None
    auth_mode: Literal['legacy_local', 'single_admin', 'local_demo', 'db_users', 'master_users'] = 'legacy_local'
    auth_master_freshness_seconds: int = Field(default=120, ge=30, le=3600)
    auth_master_pbkdf2_salt_encoding: Literal['text', 'hex', 'auto'] = 'auto'
    auth_master_resource_grants: dict[str, list[str]] = Field(default_factory=dict, repr=False)
    auth_database_url_file: Path | None = Field(default=None, repr=False)
    auth_dev_admin_bypass: bool = False
    auth_public_enabled: bool = False
    auth_origin: str = 'http://127.0.0.1:8080'
    auth_cookie_secure: bool = False
    auth_deployment_profile: Literal['none', 'localhost', 'multi_machine'] = 'none'
    auth_state_path: Path = Field(default=Path('/var/lib/etl/auth/sessions.sqlite3'), repr=False)
    admin_login_email: str = 'admin@example.invalid'
    admin_password_hash_file: Path | None = Field(default=None, repr=False)
    session_signing_key_file: Path | None = Field(default=None, repr=False)
    auth_proxy_key_file: Path | None = Field(default=None, repr=False)
    auth_idle_seconds: int = Field(default=1800, ge=60, le=86400)
    auth_absolute_seconds: int = Field(default=28800, ge=300, le=86400)
    auth_remember_seconds: int = Field(default=604800, ge=300, le=2592000)
    auth_remember_idle_seconds: int = Field(default=86400, ge=60, le=604800)
    auth_login_limit: int = Field(default=6, ge=2, le=30)
    auth_login_window_seconds: int = Field(default=60, ge=10, le=900)
    search_internal_dev_enabled: bool = False
    evaluation_max_queries: int = Field(default=20, ge=1, le=100)
    evaluation_max_corpus_versions: int = Field(default=100, ge=1, le=500)
    evaluation_max_stored_files: int = Field(default=200, ge=1, le=1000)
    evaluation_max_file_bytes: int = Field(default=8 * 1024 * 1024, ge=65536, le=16 * 1024 * 1024)
    evaluation_run_timeout_seconds: int = Field(default=600, ge=10, le=600)

    @model_validator(mode='after')
    def validate_search_grants(self):
        if self.search_internal_dev_enabled and self.app_environment != 'development':
            raise ValueError('Local demo Search is restricted to Development')
        return self

    @model_validator(mode="after")
    def validate_chunk_overlap(self):
        if self.chunk_overlap_words >= self.chunk_size_words:
            raise ValueError("CHUNK_OVERLAP_WORDS must be smaller than CHUNK_SIZE_WORDS")
        return self
    ocr_engine: Literal["native", "fake", "tesseract"] = "native"
    admin_enabled: bool = False
    upload_request_max_bytes: int = Field(default=501 * 1024 * 1024, ge=1024)

    mongo_uri: str = Field(
        default='mongodb://127.0.0.1:27017',
        repr=False,
    )
    mongo_database: str = 'etl_content'
    mongo_metadata_mode: Literal['shared', 'separate'] = 'shared'
    source_mongo_enabled: bool = False
    source_mongo_uri: str | None = Field(default=None, repr=False)
    source_mongo_database: str = Field(default='source_metadata', pattern=r'^[A-Za-z0-9_-]{1,63}$')
    master_mongo_identity_bootstrap: bool = False
    # Shared identity-only projection, independent of legacy separate bootstrap.
    master_mongo_identity_projection: bool = False
    master_mongo_identity_scope: Literal['approved_ids', 'source_verified'] = 'approved_ids'
    # Approve Master IDs, not Mongo IDs. Existing mongo_id is always authoritative.
    master_mongo_identity_ids: dict[Literal['users', 'resources'], list[str]] = Field(
        default_factory=dict, repr=False,
    )
    finalization_max_attempts: int = Field(default=3, ge=1, le=20)
    finalization_retry_seconds: int = Field(default=30, ge=1, le=3600)
    processing_manual_retry_limit: int = Field(default=3, ge=1, le=20)

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from data_sync_etl.domain.concepts import ExpansionPolicy


class Settings(BaseSettings):
    search_result_sessions_enabled: bool = False
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
    ffmpeg_path: str = 'ffmpeg'
    ffprobe_path: str = 'ffprobe'
    media_max_size_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=1024 * 1024 * 1024)
    media_max_duration_seconds: int = Field(default=1800, ge=1, le=7200)
    media_timeout_seconds: int = Field(default=900, ge=1, le=7200)
    media_cpu_threads: int = Field(default=4, ge=1, le=16)
    media_max_concurrency: int = Field(default=1, ge=1, le=4)
    media_frame_interval_seconds: float = Field(default=4, ge=.5, le=60)
    media_max_frames: int = Field(default=120, ge=1, le=600)
    media_scene_detection: bool = True
    media_scene_threshold: float = Field(default=.35, gt=0, le=1)
    media_frame_width: int = Field(default=1280, ge=320, le=1920)
    media_max_segments: int = Field(default=5000, ge=1, le=10000)
    whisper_model_path: Path | None = None
    whisper_device: Literal['cpu', 'cuda'] = 'cpu'
    whisper_compute_type: Literal['int8', 'float16', 'float32'] = 'int8'
    asr_window_seconds: int = Field(default=15, ge=2, le=30)
    translation_provider: Literal['disabled', 'argos'] = 'argos'
    translation_model_root: Path | None = None
    translation_target_language: str = Field(default='vi', pattern=r'^[a-z]{2,3}$')
    translation_timeout_seconds: int = Field(default=60, ge=1, le=300)
    translation_job_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    translation_max_retries: int = Field(default=3, ge=1, le=10)
    source_fixture_root: Path = Path("fixtures/source")
    source_adapter: Literal["json", "postgresql", "production"] = "json"
    source_database_url: str | None = Field(default=None, repr=False)
    source_migration_database_url: str | None = Field(default=None, repr=False)
    source_replay_seconds: int = Field(default=300, ge=0)
    reconcile_max_affected: int = Field(default=100, ge=1, le=1000)
    reconcile_max_ratio: float = Field(default=0.1, gt=0, le=1)
    reconciliation_scheduler_enabled: bool = False
    reconciliation_max_active: int = Field(default=1, ge=1, le=2)
    source_file_import_max_active: int = Field(default=1, ge=1, le=2)
    reconciliation_worker_heartbeat_file: Path | None = Field(default=None, repr=False)
    reconciliation_light_interval_seconds: int = Field(default=3600, ge=60, le=86400)
    reconciliation_id_sweep_interval_seconds: int = Field(default=604800, ge=3600, le=2592000)
    reconciliation_deep_coverage_days: int = Field(default=30, ge=1, le=365)
    reconciliation_audit_batch_size: int = Field(default=100, ge=1, le=1000)
    reconciliation_max_files_per_run: int = Field(default=100, ge=1, le=10000)
    reconciliation_max_bytes_per_run: int = Field(default=2147483648, ge=1, le=1099511627776)
    reconciliation_max_seconds_per_run: int = Field(default=1800, ge=1, le=86400)
    reconciliation_file_timeout_seconds: int = Field(default=120, ge=1, le=600)
    reconciliation_lease_seconds: int = Field(default=60, ge=10, le=3600)
    reconciliation_retry_max_attempts: int = Field(default=3, ge=1, le=10)
    reconciliation_retry_seconds: int = Field(default=30, ge=1, le=3600)
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
    # Explicit legacy locator -> verified Drive filename; never a global fuzzy match.
    source_drive_filename_aliases: dict[str, str] = Field(default_factory=dict, repr=False)
    source_drive_filename_policy: Literal['exact', 'canonical_id'] = 'exact'
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
    index_meaning_bags_enabled: bool = True
    index_meaning_group_threshold: float = Field(default=0.8, gt=0, le=1, allow_inf_nan=False)
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
    search_metadata_enabled: bool = False
    search_metadata_indexed_candidates_enabled: bool = False
    search_metadata_authority_limit: int = Field(default=10000, ge=201, le=20000)
    search_metadata_match_limit: int = Field(default=2000, ge=1, le=2000)
    search_metadata_content_enabled: bool = True
    search_metadata_max_resources: int = Field(default=200, ge=1, le=2000)
    source_metadata_media_enabled: bool = False
    search_metadata_auto_refresh_enabled: bool = False
    search_metadata_refresh_dense: bool = False
    search_metadata_refresh_content_semantic: bool = False
    semantic_input_identity_enabled: bool = False
    search_metadata_refresh_batch_size: int = Field(default=5, ge=1, le=50)
    search_metadata_refresh_interval_seconds: int = Field(default=30, ge=5, le=3600)
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
    # Corpus dictionary is OFF by default; a flag is never a live ACL attestation.
    concept_dynamic_enabled: bool = False
    concept_dynamic_builder_enabled: bool = False
    concept_discovery_semantic_enabled: bool = False
    concept_dynamic_artifact_root: Path | None = Field(default=None, repr=False)
    concept_corpus_mongo_uri_file: Path | None = Field(default=None, repr=False)
    concept_dynamic_lookup_budget_ms: int = Field(default=20, ge=1, le=100)
    concept_dynamic_cache_max_mb: int = Field(default=16, ge=1, le=256)
    concept_dynamic_refresh_interval_seconds: int = Field(default=10, ge=1, le=3600)
    concept_dynamic_max_lookup_records: int = Field(default=32, ge=1, le=128)
    concept_builder_batch_size: int = Field(default=20, ge=1, le=500)
    concept_builder_tick_seconds: float = Field(default=2., gt=0, le=300)
    concept_builder_max_candidates: int = Field(default=64, ge=1, le=1000)
    concept_builder_max_chars: int = Field(default=100000, ge=1000, le=1000000)
    concept_artifact_shards: int = Field(default=4, ge=1, le=16)
    concept_artifact_max_mb: int = Field(default=512, ge=1, le=4096)
    search_expansion: ExpansionPolicy = Field(default_factory=ExpansionPolicy)
    search_vector_min_similarity: float = Field(default=0.6, gt=0, le=1, allow_inf_nan=False)
    search_vector_anchor_fallback_enabled: bool = True
    search_vector_anchor_min_similarity: float = Field(default=0.4, gt=0, le=1, allow_inf_nan=False)
    search_meaning_context_fallback_enabled: bool = False
    search_meaning_fallback_min_similarity: float = Field(default=0.55, gt=0, le=1, allow_inf_nan=False)
    search_meaning_fallback_min_context_similarity: float = Field(default=0.45, gt=0, le=1, allow_inf_nan=False)
    search_meaning_context_margin: float = Field(default=0.08, ge=0, le=0.15, allow_inf_nan=False)
    search_bow_min_coverage: float = Field(default=0., ge=0, le=1, allow_inf_nan=False)
    # Experimental grouping; retain legacy bag fusion until independent acceptance.
    search_hybrid_fusion_unit: Literal['bag', 'version'] = 'bag'
    # Query-only experiment: original + at most one reviewed phrase variant.
    search_approved_accent_variants_enabled: bool = False
    search_corpus_accent_candidates_enabled: bool = False
    search_hybrid_lexical_weight: float = Field(default=1., gt=0, le=4, allow_inf_nan=False)
    search_hybrid_vector_weight: float = Field(default=1., gt=0, le=4, allow_inf_nan=False)
    search_evidence_strategy: Literal['current', 'version_dense', 'version_context'] = 'current'
    search_evidence_top_n: int = Field(default=3, ge=1, le=10)
    search_evidence_dense_margin: float = Field(default=.08, ge=0, le=.15, allow_inf_nan=False)
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
        if self.search_approved_accent_variants_enabled and self.semantic_rerank_enabled:
            raise ValueError('Approved accent variants require ColBERT OFF until combined evaluation')
        if self.search_evidence_strategy != 'current' and (not self.semantic_enabled or self.semantic_rerank_enabled
                or self.search_hybrid_fusion_unit != 'version'):
            raise ValueError('Experimental evidence requires Semantic Chunk, version fusion and ColBERT OFF')
        if (self.search_hybrid_fusion_unit != 'version'
                and (self.search_hybrid_lexical_weight != 1. or self.search_hybrid_vector_weight != 1.)):
            raise ValueError('Experimental branch weights require version fusion')
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

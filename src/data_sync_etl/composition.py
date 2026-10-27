from dataclasses import replace

from pymongo import MongoClient

from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.extraction import NativeDocumentExtractor
from data_sync_etl.adapters.media import MediaExtractor, local_model_task, probe
from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.foundation_store import (
    MongoBagStore,
    MongoContentReader,
    MongoMetadataStore,
)
from data_sync_etl.adapters.mongo.semantic import MongoSemanticStore
from data_sync_etl.adapters.ocr.engines import (
    FakeOCREngine,
    PyMuPDFPDFProcessor,
    TesseractOCREngine,
    UnconfiguredOCREngine,
)
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.adapters.persistence.subject_reader import SQLAlchemySubjectReader
from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.adapters.source.mongo_metadata import MongoMetadataSourceReader
from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
from data_sync_etl.adapters.storage.evaluation_store import EvaluationFileStore
from data_sync_etl.adapters.storage.local import LocalFilesystemStorageAdapter
from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
from data_sync_etl.adapters.storage.source_google_drive import SourceGoogleDriveFileReader
from data_sync_etl.adapters.storage.source_local import SourceLocalFileReader
from data_sync_etl.adapters.storage.source_temporary import SourceTemporaryFiles
from data_sync_etl.application.auto_sync import AutoSyncCoordinator
from data_sync_etl.application.bilingual import BilingualService
from data_sync_etl.application.capabilities import CapabilityService
from data_sync_etl.application.create_resource import CreateResourceUseCase
from data_sync_etl.application.create_resource_version import CreateResourceVersionUseCase
from data_sync_etl.application.evaluation import EvaluationService
from data_sync_etl.application.file_preview import FilePreviewService
from data_sync_etl.application.file_reconciliation import FileReconciliationJobs
from data_sync_etl.application.get_processing_job import GetProcessingJob
from data_sync_etl.application.get_resource_detail import GetResourceDetail
from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.application.ingestion import ResourceIngestion
from data_sync_etl.application.list_resource_lookups import ListResourceLookups
from data_sync_etl.application.list_resource_versions import ListResourceVersions
from data_sync_etl.application.list_resources import ListResources
from data_sync_etl.application.list_subjects_use_case import ListSubjectsUseCase
from data_sync_etl.application.master_metadata_projection import MasterMetadataProjection
from data_sync_etl.application.mongo_projection import MongoContentProjector
from data_sync_etl.application.ocr import OCRPipeline
from data_sync_etl.application.orchestrator import EtlOrchestrator
from data_sync_etl.application.postprocess import Postprocessor
from data_sync_etl.application.process_version import ProcessResourceVersion
from data_sync_etl.application.processing_finalizer import ProcessingFinalizer
from data_sync_etl.application.recommend_subjects_use_case import RecommendSubjectsUseCase
from data_sync_etl.application.reconcile_master import ReconcileMaster
from data_sync_etl.application.reconciliation_engine import ReconciliationEngine
from data_sync_etl.application.retry_processing_job import RetryProcessingJob
from data_sync_etl.application.search import SearchService
from data_sync_etl.application.semantic import SemanticService
from data_sync_etl.application.source_file_import import SourceFileImportService
from data_sync_etl.application.source_file_resolver import SourceFileResolver
from data_sync_etl.application.source_mongo_metadata_sync import SourceMongoMetadataSync
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.application.update_resource_metadata import UpdateResourceMetadataUseCase
from data_sync_etl.config import Settings
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.domain.subject import GradeBasedSubjectRecommendationPolicy
from data_sync_etl.workers.master_sync import MasterSyncWorker
from data_sync_etl.workers.ocr import OCRWorker
from data_sync_etl.workers.source_file_import import SourceFileImportWorker


def create_source(settings):
    if settings.source_adapter == "json":
        return JsonFixtureSourceAdapter(settings.source_fixture_root)
    if settings.source_adapter in {"postgresql", "production"}:
        return PostgreSQLSourceAdapter(
            settings.source_database_url, replay_seconds=settings.source_replay_seconds
        )
    raise DomainError("BLOCKED_SOURCE_MAPPING: production source adapter is not configured")


def create_ocr_engine(settings):
    if settings.ocr_engine == "fake":
        return FakeOCREngine()
    if settings.ocr_engine == "native":
        return UnconfiguredOCREngine()
    if settings.ocr_engine == "tesseract":
        return TesseractOCREngine(
            settings.tesseract_path,
            settings.tesseract_languages,
            settings.ocr_timeout_seconds,
        )
    raise DomainError("BLOCKED_OCR_RUNTIME: requested OCR engine is not configured")


class Container:
    def close(self):
        if hasattr(self, 'reconciliation_jobs'):
            self.reconciliation_jobs.close()
        if hasattr(self.source, "close"):
            self.source.close()
        if self.mongo_client is not None:
            self.mongo_client.close()
        if self.source_mongo_client is not None and self.source_mongo_client is not self.mongo_client:
            self.source_mongo_client.close()
        self.engine.dispose()

    def __init__(self, settings=None, engine=None, documents=None):
        self.settings = settings or Settings()
        if self.settings.master_mongo_identity_projection and (
                self.settings.app_environment not in ('development', 'test')
                or self.settings.mongo_metadata_mode != 'shared'
                or self.settings.source_adapter not in ('postgresql', 'production')):
            raise DomainError('MONGO_IDENTITY_PROJECTION_REQUIRES_SHARED_DEV_POSTGRES')
        if self.settings.mongo_metadata_mode == 'shared':
            if self.settings.source_mongo_enabled:
                raise DomainError('SHARED_MONGO_CONFLICT_SOURCE_ENABLED: set SOURCE_MONGO_ENABLED=false')
            if self.settings.master_mongo_identity_bootstrap:
                raise DomainError('SHARED_MONGO_CONFLICT_IDENTITY_BOOTSTRAP: set MASTER_MONGO_IDENTITY_BOOTSTRAP=false')
        self.source = create_source(self.settings)
        self.engine = engine or make_engine(self.settings.database_url)
        self.uow = SQLAlchemyUnitOfWork(sessions(self.engine))
        self.subject_reader = SQLAlchemySubjectReader(sessions(self.engine))
        self.subject_listing = ListSubjectsUseCase(self.subject_reader)
        self.subject_recommendation = RecommendSubjectsUseCase(
            self.subject_reader,
            GradeBasedSubjectRecommendationPolicy(),
        )
        self.resource_listing = ListResources(self.uow)
        self.resource_lookups = ListResourceLookups(self.uow, self.source if isinstance(self.source, PostgreSQLSourceAdapter) else None)
        self.resource_detail = GetResourceDetail(self.uow)
        self.resource_versions = ListResourceVersions(self.uow)
        self.processing_job_detail = GetProcessingJob(self.uow)
        self.storage = LocalFilesystemStorageAdapter(self.settings.storage_root)
        self.format_registry = FormatValidatorRegistry(
            media_probe=lambda path, extension: probe(path, self.settings, extension),
            media_max_size_bytes=self.settings.media_max_size_bytes)
        self.bilingual = BilingualService(self.uow, self.settings, local_model_task)
        self.resource_create = CreateResourceUseCase(self.uow, self.storage, self.format_registry, self.resource_lookups)
        self.resource_version_create = CreateResourceVersionUseCase(
            self.uow, self.storage, self.format_registry
        )
        self.update_resource_metadata = UpdateResourceMetadataUseCase(self.uow, self.resource_lookups)
        self.capabilities = CapabilityService(
            chunk_size_words = self.settings.chunk_size_words,
            chunk_overlap_words=self.settings.chunk_overlap_words,
            vector_dimension = self.settings.vector_dimension,
            ocr_engine=self.settings.ocr_engine,
            source_adapter=self.settings.source_adapter,
            metadata_backend=self.engine.dialect.name,
            mongodb_enabled=documents is None,
            auto_sync_supported=(
                isinstance(self.source, PostgreSQLSourceAdapter)
                and self.engine.dialect.name == "postgresql"
            ),
        )
        self.sync = MasterSync(self.source, self.uow, self.settings.sync_batch_size,
                               skip_shared_metadata_validation=self.settings.mongo_metadata_mode == 'shared')
        self.reconcile_master = ReconcileMaster(self.source, self.uow,
            batch_size=min(1000, self.settings.sync_batch_size),
            max_affected=self.settings.reconcile_max_affected, max_ratio=self.settings.reconcile_max_ratio)
        self.reconciliation_engine = ReconciliationEngine(
            self.uow,
            source_adapter=self.source,
            storage_root=self.settings.storage_root,
            max_ratio=self.settings.reconcile_max_ratio,
            batch_size=min(1000, self.settings.sync_batch_size),
        )
        self.auto_sync = AutoSyncCoordinator(
            self.uow,
            environment_supported=(
                isinstance(self.source, PostgreSQLSourceAdapter)
                and self.engine.dialect.name == "postgresql"
            ),
            source_health=(
                self.source.health if isinstance(self.source, PostgreSQLSourceAdapter) else None
            ),
            interval_seconds=self.settings.master_sync_auto_interval_seconds,
            worker_stale_seconds=self.settings.master_sync_auto_worker_stale_seconds,
            lease_seconds=self.settings.master_sync_auto_lease_seconds,
        )
        self.master_sync_worker = MasterSyncWorker(
            self.sync,
            self.auto_sync,
            poll_seconds=self.settings.master_sync_auto_poll_seconds,
            auth_freshness_seconds=(self.settings.auth_master_freshness_seconds
                                    if self.settings.auth_mode == 'master_users' else None),
        )
        self.ingestion = ResourceIngestion(self.uow, self.storage)
        self.ocr = OCRPipeline(
            self.uow,
            self.storage,
            PyMuPDFPDFProcessor(),
            create_ocr_engine(self.settings),
            self.settings.ocr_max_retries,
            self.settings.ocr_heartbeat_timeout_seconds,
            documents=NativeDocumentExtractor(),
            formats=self.format_registry,
            media=MediaExtractor(self.settings, self.storage, create_ocr_engine(self.settings)),
            default_target_language=self.settings.translation_target_language,
        )
        self.postprocess = Postprocessor(self.uow)
        self.orchestrator = EtlOrchestrator(
            self.sync, self.ingestion, self.ocr, self.postprocess, self.uow
        )
        self.mongo_client = None
        self.source_mongo_client = None
        self.metadata_documents = None
        self.content_reader = None
        self.content_bags = None
        if documents is None:
            self.mongo_client = MongoClient(
                self.settings.mongo_uri,
                serverSelectionTimeoutMS=3000,
                timeoutMS=10000,
                connect=False,
            )
            mongo_database = self.mongo_client[self.settings.mongo_database]
            # Invalidation is independent of model inference; snapshot guards remain authoritative.
            self.documents = MongoContentStore(mongo_database)
            self.metadata_documents = MongoMetadataStore(mongo_database)
            self.content_reader = MongoContentReader(mongo_database)
            self.content_bags = MongoBagStore(mongo_database, self.settings.index_embedding_dimension,
                historical_dimensions=self.settings.index_historical_embedding_dimensions)
        else:
            self.documents = documents

        self.master_metadata_projection = MasterMetadataProjection(
            self.uow, self.metadata_documents,
            batch_size=min(1000, self.settings.sync_batch_size),
            identity_bootstrap=(self.settings.master_mongo_identity_bootstrap
                                or self.settings.master_mongo_identity_projection),
            identity_ids=self.settings.master_mongo_identity_ids,
            identity_scope=self.settings.master_mongo_identity_scope,
            identity_guard=(self.source.metadata_identity_references
                            if isinstance(self.source, PostgreSQLSourceAdapter) else None),
        )
        identity_projection = self.master_metadata_projection
        source_metadata = None
        if self.settings.source_mongo_enabled:
            if self.mongo_client is None or not isinstance(self.source, PostgreSQLSourceAdapter):
                self.close()
                raise DomainError('SOURCE_MONGO_REQUIRES_POSTGRES_AND_MONGO_DESTINATION')
            try:
                uri = self.settings.source_mongo_uri or self.settings.mongo_uri
                self.source_mongo_client = self.mongo_client if uri == self.settings.mongo_uri else MongoClient(
                    uri, serverSelectionTimeoutMS=3000, timeoutMS=10000, connect=False)
                source_metadata = MongoMetadataSourceReader(
                    self.source_mongo_client[self.settings.source_mongo_database], mongo_database)
                # Fail configuration before any PG sync or Mongo writes.
                source_metadata.assert_distinct_destination()
            except Exception as exc:
                self.close()
                code = 'SOURCE_MONGO_SAME_DESTINATION' if isinstance(exc, DomainError) else 'SOURCE_MONGO_CONFIGURATION_UNAVAILABLE'
                raise DomainError(code) from None
        self.mongo_metadata_sync = SourceMongoMetadataSync(
            self.uow, (self.metadata_documents if self.settings.mongo_metadata_mode == 'shared' else source_metadata),
            None if self.settings.mongo_metadata_mode == 'shared' else self.metadata_documents,
            reference_guard=self.source.metadata_identity_references if isinstance(self.source, PostgreSQLSourceAdapter) else None,
            subject_guard=self.source.metadata_subject_exists if isinstance(self.source, PostgreSQLSourceAdapter) else None,
            batch_size=min(1000, self.settings.sync_batch_size),
            shared=self.settings.mongo_metadata_mode == 'shared',
        )
        if self.settings.source_mongo_enabled or self.settings.mongo_metadata_mode == 'shared':
            # By default both shared CLIs use read-only validation; separate-source
            # mode keeps its prior copy contract, never identity fallback.
            self.master_metadata_projection = self.mongo_metadata_sync
        # Normal Master Sync never invokes legacy identity bootstrap, even if an
        # old deployment still has MASTER_MONGO_IDENTITY_BOOTSTRAP=true.
        # Legacy projection remains available only through its explicit CLI.
        self.sync.metadata_projection = None if self.settings.mongo_metadata_mode == 'shared' else self.mongo_metadata_sync
        if self.settings.master_mongo_identity_projection:
            # Explicit shared Dev opt-in: identity-only, after PG COMMIT. Existing
            # shared validation remains available separately and never receives a writer.
            self.master_metadata_projection = identity_projection
            self.sync.metadata_projection = identity_projection
            self.sync.skip_shared_metadata_validation = False

        self.mongo_projector = MongoContentProjector(
            self.uow,
            self.documents,
        )

        self.indexing = IndexingService(
            self.uow, self.content_reader if documents is None else None,
            LocalSentenceEmbedding(self.settings.index_embedding_model_path,
                                   self.settings.index_embedding_model_id,
                                   self.settings.index_embedding_model_version,
                                   self.settings.index_embedding_dimension), self.settings,
            bags=self.content_bags,
        )
        self.capabilities = replace(self.capabilities, embedding_runtime=self.indexing.embedding)
        self.semantic = None
        if self.mongo_client is not None:
            self.semantic = SemanticService(self.indexing, BgeM3Embedding(
                self.settings.semantic_model_path or self.settings.index_embedding_model_path,
                self.settings.semantic_model_revision, device=self.settings.semantic_device,
                batch_size=self.settings.semantic_batch_size,
                max_tokens=self.settings.semantic_max_tokens,
                timeout_seconds=self.settings.index_timeout_seconds),
                MongoSemanticStore(mongo_database), self.settings)
        from data_sync_etl.adapters.mongo.metadata_search import MongoMetadataIndex
        from data_sync_etl.application.metadata_refresh import MetadataRefresh
        from data_sync_etl.application.metadata_search import MetadataSearchService

        self.metadata_search = (MetadataSearchService(
            self.uow, self.metadata_documents, MongoMetadataIndex(mongo_database),
            self.semantic.embedding, self.settings) if self.semantic is not None else None)
        self.master_sync_worker.metadata_refresh = MetadataRefresh(self.metadata_search, self.settings, self.semantic)
        # Attest only the fixed LocalSentenceEmbedding used by HTTP Search.
        self.search = SearchService(self.indexing, self.settings, self.content_bags,
                                    query_cache_runtime=self.indexing.embedding, semantic=self.semantic,
                                    metadata=self.metadata_search)
        if self.settings.semantic_enabled and self.semantic is not None:
            self.capabilities = replace(self.capabilities, embedding_runtime=self.semantic.embedding)
        self.evaluations = EvaluationService(self.search, EvaluationFileStore(
            self.settings.storage_root, max_files=self.settings.evaluation_max_stored_files,
            max_bytes=self.settings.evaluation_max_file_bytes), self.settings)

        self.processing_finalizer = ProcessingFinalizer(
            self.uow,
            self.postprocess,
            self.mongo_projector,
            max_attempts=self.settings.finalization_max_attempts,
            retry_seconds=self.settings.finalization_retry_seconds,
            on_content_ready=self.indexing.enqueue_auto,
        )
        self.process_version = ProcessResourceVersion(
            self.uow,
            self.ocr,
        )
        self.retry_processing_job = RetryProcessingJob(
            self.uow,
            max_manual_retries=self.settings.processing_manual_retry_limit,
        )
        self.source_files = SourceLocalFileReader(self.settings.source_file_storage_root, self.storage.root)
        if self.settings.source_drive_enabled or self.settings.source_drive_buckets or self.settings.source_local_buckets:
            self.source_files = SourceFileDispatcher(self.source_files, SourceGoogleDriveFileReader(self.settings),
                                                     local_buckets=self.settings.source_local_buckets)
        self.source_file_import = SourceFileImportService(
            self.source, self.source_files, self.storage, self.uow,
            self.process_version, self.retry_processing_job, self.settings,
            formats=FormatValidatorRegistry(media_registration=self.settings.source_metadata_media_enabled,
                                             media_processing=not self.settings.source_metadata_media_enabled,
                                             media_probe=lambda path, extension: probe(path, self.settings, extension),
                                             media_max_size_bytes=self.settings.media_max_size_bytes),
        )
        self.source_resolver = SourceFileResolver(
            self.uow, self.source, self.source_files, self.storage, self.source_file_import.formats,
            self.settings, SourceTemporaryFiles(self.settings, self.storage.root),
        )
        self.source_file_import.resolver = self.source_resolver
        self.reconciliation_engine.source_resolver = self.source_resolver
        self.reconciliation_engine.file_read_budget_seconds = self.settings.source_read_timeout_seconds
        self.reconciliation_jobs = FileReconciliationJobs(self.reconciliation_engine)
        self.file_previews = FilePreviewService(self.source_resolver, self.settings)
        self.ocr.resolver = self.source_resolver
        self.source_file_worker = SourceFileImportWorker(self.source_file_import)
        self.worker = OCRWorker(
            self.ocr,
            on_job_terminal=self.finalize_bilingual,
            reconcile=self.reconcile_processing,
            recovery_interval_seconds=self.settings.ocr_recovery_interval_seconds,
        )
        self._prefer_index_reconcile = False

    def reconcile_processing(self):
        translated = self.bilingual.once()
        self._prefer_index_reconcile = not self._prefer_index_reconcile
        if self._prefer_index_reconcile:
            return self.indexing.once() or self.processing_finalizer.reconcile_once() or translated
        return self.processing_finalizer.reconcile_once() or self.indexing.once() or translated

    def finalize_bilingual(self, job_id):
        with self.uow() as repo:
            job = repo.get('ocr_jobs', job_id)
        if job and job['status'] in ('COMPLETED', 'COMPLETED_WITH_ERRORS'):
            self.bilingual.translate_version(job['resource_version_id'])
        return self.processing_finalizer.finalize(job_id)


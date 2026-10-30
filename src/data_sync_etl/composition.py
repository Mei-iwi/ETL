import os
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
from data_sync_etl.adapters.persistence.reconciled_sync import SQLAlchemyReconciledSync
from data_sync_etl.adapters.persistence.source_identity_mapping import (
    SQLAlchemySourceIdentityMappingStore,
)
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.adapters.persistence.subject_reader import SQLAlchemySubjectReader
from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.adapters.source.mongo_metadata import MongoMetadataSourceReader
from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
from data_sync_etl.adapters.source.registry import ConfiguredSourceRegistry
from data_sync_etl.adapters.source.writeback import DisabledSourceMetadataWriter
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
from data_sync_etl.application.reconciled_sync import ReconciledSync
from data_sync_etl.application.reconciliation_engine import ReconciliationEngine
from data_sync_etl.application.retry_processing_job import RetryProcessingJob
from data_sync_etl.application.search import SearchService
from data_sync_etl.application.semantic import SemanticService
from data_sync_etl.application.source_file_import import SourceFileImportService
from data_sync_etl.application.source_file_resolver import SourceFileResolver
from data_sync_etl.application.source_metadata_writeback import SourceMetadataWriteBackService
from data_sync_etl.application.source_mongo_metadata_sync import SourceMongoMetadataSync
from data_sync_etl.application.source_open import SourceOpenResolver
from data_sync_etl.application.source_resolution import SourceResolutionService
from data_sync_etl.application.source_router import SourceRouter
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.application.update_resource_metadata import UpdateResourceMetadataUseCase
from data_sync_etl.config import Settings
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.domain.source_registry import SourceCapabilities, SourceRegistration
from data_sync_etl.domain.subject import GradeBasedSubjectRecommendationPolicy
from data_sync_etl.workers.master_sync import MasterSyncWorker
from data_sync_etl.workers.ocr import OCRWorker
from data_sync_etl.workers.source_file_import import SourceFileImportWorker


def create_source(settings):
    if settings.source_adapter == "json":
        return JsonFixtureSourceAdapter(settings.source_fixture_root)
    if settings.source_adapter in {"postgresql", "production"}:
        return PostgreSQLSourceAdapter(
            settings.source_database_url, replay_seconds=settings.source_replay_seconds,
            namespace=settings.source_namespace, timeout_seconds=settings.source_read_timeout_seconds,
        )
    raise DomainError("BLOCKED_SOURCE_MAPPING: production source adapter is not configured")


def create_source_registry(settings, source, supplied_adapters=None):
    adapters = dict(supplied_adapters or {})
    registrations = settings.source_registry_config
    if not registrations:
        locator = source.locator('resources', '__registry_probe__')
        drive_viewer_enabled = bool(settings.source_drive_enabled and settings.source_drive_buckets)
        registrations = [SourceRegistration(
            source_id='SRC_DEFAULT', backend=locator.backend, namespace=locator.namespace,
            adapter_type=type(source).__name__, enabled=True,
            capabilities=SourceCapabilities(read_metadata=True, read_content=False,
                exact_version_lookup=callable(getattr(source, 'get_version', None)),
                file_reference_lookup=drive_viewer_enabled, external_viewer=drive_viewer_enabled,
                write_metadata=False),
            viewer_hosts=('drive.google.com', 'docs.google.com') if drive_viewer_enabled else (),
        )]
        adapters['SRC_DEFAULT'] = source
    entries = []
    for raw in registrations:
        registration = (raw if isinstance(raw, SourceRegistration)
                        else SourceRegistration.model_validate(raw))
        if len(f'{registration.source_id}:resources'.encode('utf-8')) > 30:
            raise DomainError('BLOCKED_SOURCE_CHECKPOINT_KEY')
        adapter = adapters.get(registration.source_id)
        if adapter is None:
            current = source.locator('resources', '__registry_probe__')
            if (current.backend.casefold() == registration.backend.casefold()
                    and current.namespace == registration.namespace):
                adapter = source
            elif registration.enabled and registration.adapter_type.casefold() in ('postgresql', 'postgres'):
                reference = registration.connection_ref
                url = os.environ.get(reference[4:]) if reference else None
                if not url:
                    raise DomainError('SOURCE_REGISTRY_ADAPTER_UNAVAILABLE')
                adapter = PostgreSQLSourceAdapter(
                    url, replay_seconds=settings.source_replay_seconds, namespace=registration.namespace,
                    timeout_seconds=registration.timeout_seconds)
            elif registration.enabled:
                raise DomainError('SOURCE_REGISTRY_ADAPTER_UNAVAILABLE')
        entries.append((registration, adapter))
    return ConfiguredSourceRegistry(entries)


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

    def __init__(self, settings=None, engine=None, documents=None, *, source_registry=None, source_adapters=None):
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
        if source_registry is not None:
            self.source_registry = source_registry
        elif self.settings.source_federation_enabled or self.settings.source_registry_config:
            self.source_registry = create_source_registry(self.settings, self.source, source_adapters)
        else:
            source_locator = self.source.locator('resources', '__registry_probe__')
            drive_viewer_enabled = bool(self.settings.source_drive_enabled and self.settings.source_drive_buckets)
            registration = SourceRegistration(
                source_id='SRC_DEFAULT', backend=source_locator.backend, namespace=source_locator.namespace,
                adapter_type=type(self.source).__name__, enabled=True,
                capabilities=SourceCapabilities(read_metadata=True, read_content=False,
                    exact_version_lookup=callable(getattr(self.source, 'get_version', None)),
                    file_reference_lookup=drive_viewer_enabled, external_viewer=drive_viewer_enabled,
                    write_metadata=False),
                viewer_hosts=('drive.google.com', 'docs.google.com') if drive_viewer_enabled else (),
            )
            self.source_registry = ConfiguredSourceRegistry([(registration, self.source)])
        mapping_store = (SQLAlchemySourceIdentityMappingStore(self.uow, self.source_registry)
                         if self.settings.source_federation_enabled else None)
        self.source_router = SourceRouter(self.source_registry, mapping_store)
        self.source_resolution = SourceResolutionService(
            self.uow, self.source, self.settings, router=self.source_router)
        self.source_open = SourceOpenResolver(
            self.uow, self.source_registry, self.source_router, self.source_resolution)
        # A real SOURCE writer is intentionally not constructed by production
        # composition. Tests/demo may inject an isolated writer capability.
        self.source_metadata_writer = DisabledSourceMetadataWriter()
        self.source_writeback = SourceMetadataWriteBackService(
            self.uow, self.source_metadata_writer, self.settings)
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
                               skip_shared_metadata_validation=self.settings.mongo_metadata_mode == 'shared',
                               source_registry=self.source_registry,
                               federation_enabled=self.settings.source_federation_enabled)
        self.reconcile_master = ReconcileMaster(self.source, self.uow,
            batch_size=min(1000, self.settings.sync_batch_size),
            max_affected=self.settings.reconcile_max_affected, max_ratio=self.settings.reconcile_max_ratio,
            source_registry=self.source_registry,
            federation_enabled=self.settings.source_federation_enabled)
        self.reconciliation_engine = ReconciliationEngine(
            self.uow,
            source_adapter=self.source,
            storage_root=self.settings.storage_root,
            max_ratio=self.settings.reconcile_max_ratio,
            batch_size=min(1000, self.settings.sync_batch_size),
        )
        self.reconciled_sync = ReconciledSync(
            SQLAlchemyReconciledSync(self.reconciliation_engine, self.sync, self.settings)
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
            # Scheduled audits have their own worker. Auto Sync must commit real
            # users SyncRuns, including empty deltas, to maintain auth freshness.
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
            max_active=self.settings.ocr_max_active,
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
        self.source_resolution.content_authorizer = self.search.file_version
        # No live adapter can attest all PG/Mongo/provider writers yet. A flag
        # alone must never mint authority or enable stale result replay.
        self.search_sessions = None
        if self.settings.concept_dynamic_enabled:
            from data_sync_etl.application.concept_resolver import ConceptDictionaryProvider
            # No verified live revocation/RO/storage attestor exists yet. Flags
            # alone MUST NOT activate corpus aliases or start a builder/poller.
            self.search.concept_resolver = ConceptDictionaryProvider(self.search.dictionary,
                enabled=True, cache_bytes=self.settings.concept_dynamic_cache_max_mb * 1024 * 1024,
                lookup_ms=self.settings.concept_dynamic_lookup_budget_ms,
                max_records=self.settings.concept_dynamic_max_lookup_records,
                refresh_seconds=self.settings.concept_dynamic_refresh_interval_seconds)
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
        self.source_open.file_sources = self.source_files
        self.source_file_import = SourceFileImportService(
            self.source, self.source_files, self.storage, self.uow,
            self.process_version, self.retry_processing_job, self.settings,
            formats=FormatValidatorRegistry(media_registration=self.settings.source_metadata_media_enabled,
                                             media_processing=not self.settings.source_metadata_media_enabled,
                                             media_probe=lambda path, extension: probe(path, self.settings, extension),
                                             media_max_size_bytes=self.settings.media_max_size_bytes),
            source_registry=self.source_registry,
        )
        self.source_resolver = SourceFileResolver(
            self.uow, self.source, self.source_files, self.storage, self.source_file_import.formats,
            self.settings, SourceTemporaryFiles(self.settings, self.storage.root), self.source_registry,
        )
        self.source_file_import.resolver = self.source_resolver
        self.search.source_content_authorizer = self.source_resolver.derived_index_authorized
        self.indexing.source_content_authorizer = self.source_resolver.derived_index_authorized
        self.reconciliation_engine.source_resolver = self.source_resolver
        self.reconciliation_engine.file_read_budget_seconds = self.settings.source_read_timeout_seconds
        self.reconciliation_engine.audit_settings = self.settings
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


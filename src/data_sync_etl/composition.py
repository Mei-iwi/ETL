from pymongo import MongoClient

from data_sync_etl.adapters.extraction import NativeDocumentExtractor
from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.foundation_store import (
    MongoBagStore,
    MongoContentReader,
    MongoMetadataStore,
)
from data_sync_etl.adapters.ocr.engines import (
    FakeOCREngine,
    PyMuPDFPDFProcessor,
    TesseractOCREngine,
    UnconfiguredOCREngine,
)
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.adapters.persistence.subject_reader import SQLAlchemySubjectReader
from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
from data_sync_etl.adapters.storage.local import LocalFilesystemStorageAdapter
from data_sync_etl.adapters.storage.source_local import SourceLocalFileReader
from data_sync_etl.application.auto_sync import AutoSyncCoordinator
from data_sync_etl.application.capabilities import CapabilityService
from data_sync_etl.application.create_resource import CreateResourceUseCase
from data_sync_etl.application.create_resource_version import CreateResourceVersionUseCase
from data_sync_etl.application.get_processing_job import GetProcessingJob
from data_sync_etl.application.get_resource_detail import GetResourceDetail
from data_sync_etl.application.ingestion import ResourceIngestion
from data_sync_etl.application.list_resource_lookups import ListResourceLookups
from data_sync_etl.application.list_resource_versions import ListResourceVersions
from data_sync_etl.application.list_resources import ListResources
from data_sync_etl.application.list_subjects_use_case import ListSubjectsUseCase
from data_sync_etl.application.mongo_projection import MongoContentProjector
from data_sync_etl.application.ocr import OCRPipeline
from data_sync_etl.application.orchestrator import EtlOrchestrator
from data_sync_etl.application.postprocess import Postprocessor
from data_sync_etl.application.process_version import ProcessResourceVersion
from data_sync_etl.application.processing_finalizer import ProcessingFinalizer
from data_sync_etl.application.recommend_subjects_use_case import RecommendSubjectsUseCase
from data_sync_etl.application.retry_processing_job import RetryProcessingJob
from data_sync_etl.application.source_file_import import SourceFileImportService
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
        if hasattr(self.source, "close"):
            self.source.close()
        if self.mongo_client is not None:
            self.mongo_client.close()
        self.engine.dispose()

    def __init__(self, settings=None, engine=None, documents=None):
        self.settings = settings or Settings()
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
        self.resource_lookups = ListResourceLookups(self.uow)
        self.resource_detail = GetResourceDetail(self.uow)
        self.resource_versions = ListResourceVersions(self.uow)
        self.processing_job_detail = GetProcessingJob(self.uow)
        self.storage = LocalFilesystemStorageAdapter(self.settings.storage_root)
        self.format_registry = FormatValidatorRegistry()
        self.resource_create = CreateResourceUseCase(self.uow, self.storage, self.format_registry)
        self.resource_version_create = CreateResourceVersionUseCase(
            self.uow, self.storage, self.format_registry
        )
        self.update_resource_metadata = UpdateResourceMetadataUseCase(self.uow)
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
        self.sync = MasterSync(self.source, self.uow, self.settings.sync_batch_size)
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
        )
        self.postprocess = Postprocessor(self.uow)
        self.orchestrator = EtlOrchestrator(
            self.sync, self.ingestion, self.ocr, self.postprocess, self.uow
        )
        self.mongo_client = None
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
            self.documents = MongoContentStore(mongo_database)
            self.metadata_documents = MongoMetadataStore(mongo_database)
            self.content_reader = MongoContentReader(mongo_database)
            self.content_bags = MongoBagStore(mongo_database, self.settings.vector_dimension)
        else:
            self.documents = documents

        self.mongo_projector = MongoContentProjector(
            self.uow,
            self.documents,
        )

        self.processing_finalizer = ProcessingFinalizer(
            self.uow,
            self.postprocess,
            self.mongo_projector,
            max_attempts=self.settings.finalization_max_attempts,
            retry_seconds=self.settings.finalization_retry_seconds,
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
        self.source_file_import = SourceFileImportService(
            self.source, self.source_files, self.storage, self.uow,
            self.process_version, self.retry_processing_job, self.settings,
            formats=self.format_registry,
        )
        self.source_file_worker = SourceFileImportWorker(self.source_file_import)
        self.worker = OCRWorker(
            self.ocr,
            on_job_terminal=self.processing_finalizer.finalize,
            reconcile=self.processing_finalizer.reconcile_once,
            recovery_interval_seconds=self.settings.ocr_recovery_interval_seconds,
        )


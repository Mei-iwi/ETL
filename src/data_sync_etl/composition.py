from pymongo import MongoClient

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.ocr.engines import (
    FakeOCREngine,
    PyMuPDFPDFProcessor,
    TesseractOCREngine,
    UnconfiguredOCREngine,
)
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.adapters.storage.local import LocalFilesystemStorageAdapter
from data_sync_etl.application.capabilities import CapabilityService
from data_sync_etl.application.create_resource import CreateResourceUseCase
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.application.ingestion import ResourceIngestion
from data_sync_etl.application.list_resources import ListResources
from data_sync_etl.application.mongo_projection import MongoContentProjector
from data_sync_etl.application.ocr import OCRPipeline
from data_sync_etl.application.orchestrator import EtlOrchestrator
from data_sync_etl.application.postprocess import Postprocessor
from data_sync_etl.application.process_version import ProcessResourceVersion
from data_sync_etl.application.processing_finalizer import ProcessingFinalizer
from data_sync_etl.application.retry_processing_job import RetryProcessingJob
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.config import Settings
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.core import DomainError
from data_sync_etl.workers.ocr import OCRWorker


def create_source(settings):
    if settings.source_adapter == "json":
        return JsonFixtureSourceAdapter(settings.source_fixture_root)
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
    def __init__(self, settings=None, engine=None, documents=None):
        self.settings = settings or Settings()
        self.source = create_source(self.settings)
        self.engine = engine or make_engine(self.settings.database_url)
        self.uow = SQLAlchemyUnitOfWork(sessions(self.engine))
        self.resource_listing = ListResources(self.uow)
        self.format_registry = FormatValidatorRegistry()
        self.storage = LocalFilesystemStorageAdapter(self.settings.storage_root)
        self.resource_create = CreateResourceUseCase(self.uow, self.storage, self.format_registry)
        self.capabilities = CapabilityService(
            chunk_size_words = self.settings.chunk_size_words,
            chunk_overlap_words=self.settings.chunk_overlap_words,
            vector_dimension = self.settings.vector_dimension,
            ocr_engine=self.settings.ocr_engine,

        )
        self.sync = MasterSync(self.source, self.uow, self.settings.sync_batch_size)
        self.ingestion = ResourceIngestion(self.uow, self.storage)
        self.ocr = OCRPipeline(
            self.uow,
            self.storage,
            PyMuPDFPDFProcessor(),
            create_ocr_engine(self.settings),
            self.settings.ocr_max_retries,
            self.settings.ocr_heartbeat_timeout_seconds,
        )
        self.postprocess = Postprocessor(self.uow)
        self.orchestrator = EtlOrchestrator(
            self.sync, self.ingestion, self.ocr, self.postprocess, self.uow
        )
        self.mongo_client = None
        if documents is None:
            self.mongo_client = MongoClient(
                self.settings.mongo_uri,
                serverSelectionTimeoutMS=3000,
                timeoutMS=10000,
                connect=False,
            )
            mongo_database = self.mongo_client[self.settings.mongo_database]
            self.documents = MongoContentStore(mongo_database)
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
        self.worker = OCRWorker(
            self.ocr,
            on_job_terminal=self.processing_finalizer.finalize,
            reconcile=self.processing_finalizer.reconcile_once,
            recovery_interval_seconds=self.settings.ocr_recovery_interval_seconds,
        )


# Endpoint → Use case → Port → Adapter → Storage

Audit tĩnh HEAD 18bad82 (cùng tree với điểm tạo nhánh 9643c6b). [endpoint_inventory.csv](endpoint_inventory.csv) liệt kê 40 path/handler/line. Không gọi HTTP hoặc worker; READY chỉ là điều kiện trong code, không phải kết quả live.

Code path tương đối src/data_sync_etl/. Composition root: composition.py:170 trở đi.
UoW = ports/repositories.UnitOfWork → adapters/persistence/sqlalchemy.SQLAlchemyUnitOfWork/SQLAlchemyRepository → Master PG.

| Endpoint / handler nhóm | Application | Port | Adapter / đích | Frontend / điều kiện |
| --- | --- | --- | --- | --- |
| GET /health; /api/v1/system/capabilities | Health trực tiếp; CapabilityService | Runtime checks / repository | PG health, format/OCR/model | SystemStatus/Capabilities; không chứng nhận index READY |
| GET /api/v1/subjects; /subjects/recommended | ListSubjectsUseCase, RecommendSubjectsUseCase | SubjectReader | SQLAlchemySubjectReader → Master subjects/grades | Library forms |
| GET /api/v1/resource-types; /providers; /grade-levels | ListResourceLookups | UnitOfWork | Master snapshots/grades | Provider/type chỉ coverage đã dùng |
| GET /api/v1/resources; /resources/{id}; /resources/{id}/versions | ListResources, GetResourceDetail, ListResourceVersions | UnitOfWork, ResourceMetadataRepository | SQLAlchemyRepository → Master resources/version/import | LibraryList/Detail/History |
| PATCH /api/v1/resources/{id}/metadata | UpdateResourceMetadataUseCase | UnitOfWork | ResourceMetadataRepositoryMixin → Master | Chỉ LOCAL, nguồn/UNKNOWN fail closed |
| POST /api/v1/resources | CreateResourceUseCase | UnitOfWork, StoragePort | Master + LocalFilesystemStorageAdapter | LibraryCreate; validation trước persistence |
| POST /api/v1/resource/{id}/versions | CreateResourceVersionUseCase | UnitOfWork, StoragePort | Master + storage đích | LibraryUpdateVersion; giữ path số ít tương thích |
| POST /api/v1/resource-versions/{id}/process | ProcessResourceVersion → OCRPipeline | UnitOfWork, PDFProcessorPort, DocumentExtractorPort, OCREnginePort | PG jobs/tasks; PyMuPDF/NativeDocumentExtractor/Tesseract | LibraryProcessing; format/runtime hợp lệ |
| GET /api/v1/processing-jobs/{job_id}; POST /processing-jobs/{job_id}/retry | GetProcessingJob, RetryProcessingJob | UnitOfWork | Master job/task/stage/retry | Poll trạng thái; guard retry hiện tại |
| POST /api/v1/master-data/sync; /admin/sync/master alias | MasterSync | SourceDataReader, ReconciliationSource, UnitOfWork, metadata ports | PostgreSQLSourceAdapter SELECT → Master; Mongo sau commit | SystemMasterSync; 4 streams |
| GET /api/v1/master-data/sync/status; PATCH /sync/auto | MasterSync, AutoSyncCoordinator, import availability | UnitOfWork/source read ports | Master sync_*; SOURCE read-only | Bật auto không tự chứng minh worker đang chạy |
| GET /api/v1/master-data/sync/files | SourceFileImportService.list_files | ReadOnlySourceVersionPort, UnitOfWork | PG source.version_page + source_file_imports | Đồng bộ/Lỗi đồng bộ; không đọc danh sách từ bags |
| POST /api/v1/master-data/sync/files/retry-failed; /files/{file_id}/retry | SourceFileImportService.retry_failed/retry | UnitOfWork, ReadOnlySourceVersionPort | Import queue Master, không UPDATE SOURCE | LibrarySyncErrors; unsupported/mutated/mapping guard |
| GET /api/v1/resource-versions/{id}/content-units; /admin/resource-versions/{id}/content-units | EtlOrchestrator.content_units | UnitOfWork | Master content_units/content_unit_texts | LibraryProcessing; thiếu SearchAccess (B05) |
| POST /api/v1/resource-versions/{id}/index; /reindex | IndexingService.enqueue | UnitOfWork, IndexContentPort, IndexEmbeddingPort, ContentBagPort | PG etl_stage_runs; MongoContentReader/MongoBagStore; LocalSentenceEmbedding | LibraryIndexing; CONTENT_READY + schema/model gate |
| GET /api/v1/resource-versions/{id}/index-status | IndexingService.status | UoW, ContentBagPort/runtime | PG stages, Mongo bags/fingerprint | Phân biệt blocker hiện tại/lịch sử |
| GET /api/v1/resource-versions/{id}/chunks | IndexingService.chunks | IndexContentPort, ContentBagPort, UoW | Mongo CU/bags + Master version/units | Current generation/fingerprint; không indexed_chunks |
| POST /api/v1/search | SearchService | SearchBagReader, SearchMetadataReader; content/embedding qua IndexingService | MongoBagStore + SQLAlchemyRepository + local model | QueryMethodPage; BoW/Vector/Hybrid cùng policy |
| GET /api/v1/chunks/{context_id}/context | SearchService.context | SearchMetadataReader, IndexContentPort | PG policy + Mongo CU | Không vượt policy Search |
| GET /api/v1/resource-versions/{id}/file | SearchService.file_version + storage | UoW, StoragePort | Master metadata/text + file đích | Detail/Search; SearchAccess trước trả file |
| POST /api/v1/evaluations/datasets | EvaluationService | EvaluationStorePort, SearchService | EvaluationFileStore trên STORAGE_ROOT | EvaluationDataPage; dataset/nhãn validation |
| POST /api/v1/evaluations/runs | EvaluationService.run/run_events | EvaluationStorePort, SearchService | FileStore + Search PG/Mongo | RunExperimentPage; corpus guard, timeout, streaming progress |
| GET /api/v1/evaluations/runs/{id}; /runs/{id}/report | EvaluationService.get_run/report | EvaluationStorePort | FileStore read | Results/Reports, không thêm collection |
| POST /admin/resources/{id}/versions/ingest | ResourceIngestion | UoW, StoragePort | Master versions + file | Legacy admin giữ nguyên |
| POST /admin/resource-versions/{id}/ocr-jobs | OCRPipeline.create_job | UoW, PDF/OCR ports | Master job/tasks + storage | Legacy admin |
| POST /admin/resource-versions/{id}/postprocess | Postprocessor | UoW | Master normalized text/CU/stage | Legacy admin |
| GET /admin/resource-versions/{id}/etl-status | EtlOrchestrator.status | UoW | Master resource/version/job/stage | Legacy admin; chưa auth Production |

Path rút gọn cùng hàng kế thừa tiền tố của endpoint đầu; CSV lưu đầy đủ, không dùng bảng này để tạo route mới.

## Worker / ba tầng — chỉ đọc code, không khởi chạy

| Bước | Use case / worker | Port / adapter / trạng thái |
| --- | --- | --- |
| SOURCE metadata → Master | MasterSync, MasterSyncWorker, ReconcileMaster | Snapshot read-only; gộp danh mục, checkpoint/ownership, reconciliation xóa logic |
| File nguồn → storage đích | SourceFileImportService, SourceFileImportWorker | ReadOnlySourceVersionPort/ReadOnlySourceFilePort; SourceFileDispatcher → SourceLocalFileReader/SourceGoogleDriveFileReader drive.readonly; hash |
| OCR/extraction | OCRWorker/OCRPipeline | File đích → Master job/task/result, không copy lịch sử SOURCE OCR |
| Chuẩn hóa | ProcessingFinalizer → Postprocessor | UNREVIEWED + retrieval_eligible=false; không mặc định quyền public |
| Mongo projection | MongoContentProjector | ContentDocumentPort → MongoContentStore; generation/current fencing |
| Bag/Index | finalizer.on_content_ready → enqueue_auto; worker reconcile | ContentBagBuilder, keywords, embedding 384; READY khi schema/model/generation/fingerprint hợp lệ |
| Search/Context | SearchService | Chỉ read corpus được policy cho phép; không Reindex để cấp quyền |
| Thực nghiệm | EvaluationService | Nhãn độc lập, corpus snapshot, metrics/latency; điểm Search không làm Ground Truth |

## Thuật ngữ / ownership

- SOURCE PG nguyên cấp: metadata, danh mục và tham chiếu file.
- Master thứ cấp: snapshot gộp provider/type/resource, role/user, education/grade; N-N và runtime riêng.
- LOV: giá trị danh mục để chọn; provider/type hiện chưa bảo đảm toàn bộ SOURCE catalog.
- ContentBag: túi từ và embedding sau xử lý, không phải danh mục/nguồn gốc.
- mongo_id: mapping logic xuyên DB, không FK SQL; không ép mọi _id bằng ID SOURCE.
- Shared metadata mặc định validate mapping/extension; projection identity chỉ opt-in; không phát sinh dữ liệu giả.
- Local/COURSE_DRIVE là storage đầu vào. Drive chỉ read, không upload đầu ra.
- React Router/AppLayout, submenu và pages hiện tại giữ nguyên.

Live API/Frontend/OCR/model/Drive/auto-index: NOT_RUN. Bảng mô tả đường code không khẳng định dữ liệu hiện tại READY.

# API inventory ? G0

Offline registered route inspection + dynamic-template source scan. No startup/DB/.env.
All routes KEEP; external consumers are UNKNOWN unless explicitly approved. Tests are usage evidence, not production clients.
CLI/worker invoke shared application services, not HTTP; service_refs traces these without falsely marking their handlers dead.

| Method | Path | Handler | Usage | Decision | Call sites |
| --- | --- | --- | --- | --- | --- |
| GET | /health | health | USED_FRONTEND | KEEP | README.md:357, frontend/src/components/layout/Header.tsx:17, frontend/src/pages/system/SystemStatusPage.tsx:34, tests/unit/test_api_migrations.py:16 |
| POST | /api/v1/search | search_materials | USED_FRONTEND | KEEP | frontend/src/services/searchApi.ts:37, tests/integration/source_postgres/test_content_pipeline_acceptance.py:198, tests/integration/source_postgres/test_content_pipeline_acceptance.py:311, tests/integration/test_search_integration.py:25 |
| POST | /api/v1/evaluations/datasets | create_evaluation_dataset | USED_TEST_SCRIPT | KEEP | tests/integration/source_postgres/test_content_pipeline_acceptance.py:236, tests/unit/test_evaluation.py:34, tests/unit/test_evaluation.py:60, tests/unit/test_evaluation.py:86 |
| POST | /api/v1/evaluations/runs | run_evaluation | USED_TEST_SCRIPT | KEEP | tests/integration/source_postgres/test_content_pipeline_acceptance.py:239, tests/integration/source_postgres/test_content_pipeline_acceptance.py:243, tests/integration/source_postgres/test_content_pipeline_acceptance.py:256, tests/integration/source_postgres/test_content_pipeline_acceptance.py:292 |
| GET | /api/v1/evaluations/runs/{id} | get_evaluation_run | USED_TEST_SCRIPT | KEEP | tests/integration/source_postgres/test_content_pipeline_acceptance.py:243, tests/integration/source_postgres/test_content_pipeline_acceptance.py:256, tests/unit/test_evaluation.py:44, tests/unit/test_evaluation.py:51 |
| GET | /api/v1/evaluations/runs/{id}/report | export_evaluation_report | USED_TEST_SCRIPT | KEEP | tests/integration/source_postgres/test_content_pipeline_acceptance.py:256, tests/unit/test_evaluation.py:51, tests/unit/test_evaluation.py:54, tests/unit/test_evaluation.py:63 |
| GET | /api/v1/chunks/{context_id}/context | chunk_context | USED_FRONTEND | KEEP | frontend/src/services/searchApi.ts:41, src/data_sync_etl/application/search.py:162, tests/integration/source_postgres/test_content_pipeline_acceptance.py:203, tests/unit/test_search_access.py:68 |
| GET | /api/v1/resource-versions/{id}/file | resource_file | USED_FRONTEND | KEEP | frontend/src/pages/library/ResourceFilePanel.tsx:36, tests/unit/test_resource_file.py:17, tests/unit/test_resource_file.py:54, tests/unit/test_resource_file.py:61 |
| GET | /api/v1/system/capabilities | system_capabilities | USED_FRONTEND | KEEP | frontend/src/pages/system/SystemCapalitiesPage.tsx:72, tests/unit/test_api_migrations.py:119, tests/unit/test_media_out_of_scope.py:142 |
| GET | /api/v1/subjects | list_subjects | USED_FRONTEND | KEEP | frontend/src/services/libraryLookupApi.ts:91, tests/unit/test_subject_api.py:11, tests/unit/test_subject_api.py:26, tests/unit/test_subject_api.py:40 |
| GET | /api/v1/subjects/recommended | recommended_subjects | USED_TEST_SCRIPT | KEEP | tests/unit/test_subject_api.py:54, tests/unit/test_subject_api.py:67 |
| GET | /api/v1/resource-types | list_resource_types | USED_FRONTEND | KEEP | frontend/src/services/libraryLookupApi.ts:79, tests/unit/test_resource_lookups.py:20, tests/unit/test_resource_lookups.py:53, tests/unit/test_resource_lookups.py:77 |
| GET | /api/v1/providers | list_providers | USED_FRONTEND | KEEP | frontend/src/services/libraryLookupApi.ts:83, tests/unit/test_resource_lookups.py:21, tests/unit/test_resource_lookups.py:54, tests/unit/test_resource_lookups.py:78 |
| GET | /api/v1/grade-levels | list_grade_levels | USED_FRONTEND | KEEP | frontend/src/services/libraryLookupApi.ts:87, tests/unit/test_resource_lookups.py:22, tests/unit/test_resource_lookups.py:55, tests/unit/test_resource_lookups.py:132 |
| GET | /api/v1/resources | list_resources | USED_FRONTEND | KEEP | frontend/src/pages/library/LibraryCreatePage.tsx:152, frontend/src/pages/library/LibraryListPage.tsx:94, frontend/src/pages/library/LibraryResourceDetailPage.tsx:61, frontend/src/pages/library/LibraryUpdateVersion.tsx:84 |
| GET | /api/v1/resources/{id} | get_resource | USED_FRONTEND | KEEP | frontend/src/pages/library/LibraryResourceDetailPage.tsx:61, frontend/src/pages/library/LibraryUpdateVersion.tsx:84, frontend/src/pages/library/LibraryVersionHistoryPage.tsx:49, frontend/src/pages/library/LibraryVersionHistoryPage.tsx:52 |
| GET | /api/v1/resources/{id}/versions | list_resource_versions | USED_FRONTEND | KEEP | frontend/src/pages/library/LibraryVersionHistoryPage.tsx:52, frontend/src/pages/library/ResourceFilePanel.tsx:22, frontend/src/services/indexingApi.ts:40, tests/integration/source_postgres/test_delete_reconciliation.py:152 |
| GET | /api/v1/processing-jobs/{job_id} | get_processing_job | USED_FRONTEND | KEEP | frontend/src/services/processingApi.ts:84, frontend/src/services/processingApi.ts:105, tests/integration/test_processing_retry_integration.py:53, tests/integration/test_processing_retry_integration.py:56 |
| PATCH | /api/v1/resources/{id}/metadata | update_resource_metadata | USED_FRONTEND | KEEP | frontend/src/services/resourceApi.ts:60, tests/integration/source_postgres/test_resource_ownership.py:21, tests/integration/source_postgres/test_source.py:314, tests/unit/test_resource_metadata_ownership.py:44 |
| POST | /api/v1/resources | create_resource | USED_FRONTEND | KEEP | frontend/src/pages/library/LibraryCreatePage.tsx:152, frontend/src/pages/library/LibraryListPage.tsx:94, frontend/src/pages/library/LibraryResourceDetailPage.tsx:61, frontend/src/pages/library/LibraryUpdateVersion.tsx:84 |
| POST | /api/v1/resource/{id}/versions | create_resource_version | USED_FRONTEND | KEEP | frontend/src/services/resourceApi.ts:85, tests/unit/test_create_resource_version.py:12, tests/unit/test_create_resource_version.py:26, tests/unit/test_create_resource_version.py:39 |
| POST | /api/v1/resource-versions/{id}/process | process_resource_version | USED_FRONTEND | KEEP | frontend/src/services/processingApi.ts:60, tests/integration/source_postgres/test_runtime.py:36, tests/integration/test_mongo_processing.py:61, tests/integration/test_mongo_processing.py:107 |
| POST | /api/v1/processing-jobs/{job_id}/retry | retry_processing_job | USED_FRONTEND | KEEP | frontend/src/services/processingApi.ts:84, tests/integration/test_processing_retry_integration.py:53, tests/integration/test_processing_retry_integration.py:56, tests/integration/test_processing_retry_integration.py:68 |
| POST | /api/v1/master-data/sync | sync_master_data | USED_FRONTEND | KEEP | frontend/src/services/masterSyncApi.ts:26, frontend/src/services/masterSyncApi.ts:36, frontend/src/services/masterSyncApi.ts:45, frontend/src/services/masterSyncApi.ts:56 |
| GET | /api/v1/master-data/sync/status | master_sync_status | USED_FRONTEND | KEEP | frontend/src/services/masterSyncApi.ts:75, tests/unit/test_api_migrations.py:21, tests/unit/test_api_migrations.py:45, tests/unit/test_api_migrations.py:57 |
| PATCH | /api/v1/master-data/sync/auto | configure_master_sync_auto | USED_FRONTEND | KEEP | frontend/src/services/masterSyncApi.ts:92, tests/unit/test_api_migrations.py:23, tests/unit/test_api_migrations.py:59, tests/unit/test_api_migrations.py:62 |
| GET | /api/v1/master-data/sync/files | source_sync_files | USED_FRONTEND | KEEP | frontend/src/services/masterSyncApi.ts:26, frontend/src/services/masterSyncApi.ts:36, frontend/src/services/masterSyncApi.ts:45, tests/unit/test_source_file_import.py:235 |
| POST | /api/v1/master-data/sync/files/retry-failed | retry_failed_source_files | USED_FRONTEND | KEEP | frontend/src/services/masterSyncApi.ts:45, tests/unit/test_source_file_import.py:238, tests/unit/test_source_file_import.py:243 |
| POST | /api/v1/master-data/sync/files/{file_id}/retry | retry_source_file | USED_FRONTEND | KEEP | frontend/src/services/masterSyncApi.ts:36, tests/unit/test_source_file_import.py:240, tests/unit/test_source_file_import.py:241 |
| POST | /admin/sync/master | sync | USED_TEST_SCRIPT | KEEP | tests/unit/test_api_migrations.py:19, tests/unit/test_api_migrations.py:71, tests/unit/test_api_migrations.py:147, tests/unit/test_api_migrations.py:150 |
| POST | /api/v1/resource-versions/{id}/index | index_version | USED_FRONTEND | KEEP | frontend/src/pages/library/ResourceFilePanel.tsx:45, frontend/src/services/indexingApi.ts:31, frontend/src/services/indexingApi.ts:50, src/data_sync_etl/application/indexing.py:180 |
| POST | /admin/resources/{id}/versions/ingest | ingest | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| GET | /api/v1/resource-versions/{id}/chunks | chunk_preview | USED_FRONTEND | KEEP | frontend/src/services/indexingApi.ts:46, tests/unit/test_indexing.py:96 |
| POST | /admin/resource-versions/{id}/ocr-jobs | create_job | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| GET | /api/v1/resource-versions/{id}/index-status | index_status | USED_FRONTEND | KEEP | frontend/src/pages/library/ResourceFilePanel.tsx:45, frontend/src/services/indexingApi.ts:50, src/data_sync_etl/application/indexing.py:180 |
| POST | /admin/resource-versions/{id}/postprocess | postprocess | USED_TEST_SCRIPT | KEEP | tests/unit/test_process_version.py:324 |
| POST | /api/v1/resource-versions/{id}/reindex | reindex_version | USED_FRONTEND | KEEP | frontend/src/services/indexingApi.ts:54 |
| GET | /admin/resource-versions/{id}/etl-status | status | USED_TEST_SCRIPT | KEEP | src/data_sync_etl/application/process_version.py:47, src/data_sync_etl/application/retry_processing_job.py:22, tests/unit/test_api_migrations.py:152, tests/unit/test_boundaries.py:26 |
| GET | /api/v1/resource-versions/{id}/content-units | list_public_content_units | USED_FRONTEND | KEEP | frontend/src/services/processingApi.ts:132, tests/unit/test_media_out_of_scope.py:98, tests/unit/test_media_out_of_scope.py:109, tests/unit/test_multiformat.py:170 |
| GET | /admin/resource-versions/{id}/content-units | content_units | USED_TEST_SCRIPT | KEEP | tests/unit/test_content_units_api.py:14, tests/unit/test_content_units_api.py:44, tests/unit/test_content_units_api.py:49 |
| GET | /openapi.json | openapi | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| HEAD | /openapi.json | openapi | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| GET | /docs | swagger_ui_html | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| HEAD | /docs | swagger_ui_html | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| GET | /docs/oauth2-redirect | swagger_ui_redirect | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| HEAD | /docs/oauth2-redirect | swagger_ui_redirect | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| GET | /redoc | redoc_html | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |
| HEAD | /redoc | redoc_html | EXTERNALLY_EXPOSED_UNKNOWN | KEEP |  |

# Security matrix ? baseline G0

Local admin config is NOT authentication. PUBLISHED is NOT public rights. No production identity provider.
Middleware currently only limits POST /resources. Handler checks can occur after multipart parsing.

| Method/path | Category | Current guard calls | Risk / intended boundary |
| --- | --- | --- | --- |
| GET /health | READ_PUBLIC | NONE | Preserve public-safe DTO |
| POST /api/v1/search | SEARCH_CONTEXT | search_access | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/evaluations/datasets | ADMIN_WRITE | evaluation_services | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/evaluations/runs | ADMIN_WRITE | evaluation_services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/evaluations/runs/{id} | ADMIN_READ | evaluation_services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/evaluations/runs/{id}/report | ADMIN_READ | evaluation_services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/chunks/{context_id}/context | SEARCH_CONTEXT | search_access | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resource-versions/{id}/file | FILE_STREAM | search_access | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/system/capabilities | READ_PUBLIC | NONE | Preserve public-safe DTO |
| GET /api/v1/subjects | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/subjects/recommended | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resource-types | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/providers | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/grade-levels | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resources | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resources/{id} | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resources/{id}/versions | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/processing-jobs/{job_id} | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| PATCH /api/v1/resources/{id}/metadata | ADMIN_WRITE | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/resources | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/resource/{id}/versions | ADMIN_WRITE | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/resource-versions/{id}/process | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/processing-jobs/{job_id}/retry | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/master-data/sync | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/master-data/sync/status | ADMIN_READ | services | Verified local-only; shared content access semantics required; no client identity grants |
| PATCH /api/v1/master-data/sync/auto | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/master-data/sync/files | ADMIN_READ | source_file_admin | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/master-data/sync/files/retry-failed | ADMIN_WRITE | source_file_admin | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/master-data/sync/files/{file_id}/retry | ADMIN_WRITE | source_file_admin | Verified local-only; shared content access semantics required; no client identity grants |
| POST /admin/sync/master | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/resource-versions/{id}/index | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| POST /admin/resources/{id}/versions/ingest | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resource-versions/{id}/chunks | READ_SENSITIVE | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| POST /admin/resource-versions/{id}/ocr-jobs | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resource-versions/{id}/index-status | ADMIN_READ | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| POST /admin/resource-versions/{id}/postprocess | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| POST /api/v1/resource-versions/{id}/reindex | ADMIN_WRITE | services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /admin/resource-versions/{id}/etl-status | ADMIN_READ | services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /api/v1/resource-versions/{id}/content-units | READ_SENSITIVE | NONE | Verified local-only; shared content access semantics required; no client identity grants |
| GET /admin/resource-versions/{id}/content-units | READ_SENSITIVE | services | Verified local-only; shared content access semantics required; no client identity grants |
| GET /openapi.json | READ_PUBLIC | NONE | Preserve public-safe DTO |
| HEAD /openapi.json | READ_PUBLIC | NONE | Preserve public-safe DTO |
| GET /docs | READ_PUBLIC | NONE | Preserve public-safe DTO |
| HEAD /docs | READ_PUBLIC | NONE | Preserve public-safe DTO |
| GET /docs/oauth2-redirect | READ_PUBLIC | NONE | Preserve public-safe DTO |
| HEAD /docs/oauth2-redirect | READ_PUBLIC | NONE | Preserve public-safe DTO |
| GET /redoc | READ_PUBLIC | NONE | Preserve public-safe DTO |
| HEAD /redoc | READ_PUBLIC | NONE | Preserve public-safe DTO |

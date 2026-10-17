# Security matrix - after G1

Production authentication is NOT implemented. Local administration requires verified launch and loopback request; Search/Context/File/Evaluation retain their independent access policy. HTTP method/path and DTO schemas are unchanged.

| Method/path | Boundary | Guard calls |
| --- | --- | --- |
| GET /health | public safe read | public-safe DTO |
| POST /api/v1/search | handler policy | search_access |
| POST /api/v1/evaluations/datasets | early local-admin ASGI | evaluation_services |
| POST /api/v1/evaluations/runs | early local-admin ASGI | evaluation_services |
| GET /api/v1/evaluations/runs/{id} | handler policy | evaluation_services |
| GET /api/v1/evaluations/runs/{id}/report | handler policy | evaluation_services |
| GET /api/v1/chunks/{context_id}/context | handler policy | search_access |
| GET /api/v1/resource-versions/{id}/file | handler policy | search_access |
| GET /api/v1/system/capabilities | public safe read | public-safe DTO |
| GET /api/v1/subjects | public safe read | public-safe DTO |
| GET /api/v1/subjects/recommended | public safe read | public-safe DTO |
| GET /api/v1/resource-types | public safe read | public-safe DTO |
| GET /api/v1/providers | public safe read | public-safe DTO |
| GET /api/v1/grade-levels | public safe read | public-safe DTO |
| GET /api/v1/resources | early local-admin ASGI | public-safe DTO |
| GET /api/v1/resources/{id} | early local-admin ASGI | public-safe DTO |
| GET /api/v1/resources/{id}/versions | early local-admin ASGI | public-safe DTO |
| GET /api/v1/processing-jobs/{job_id} | early local-admin ASGI | public-safe DTO |
| PATCH /api/v1/resources/{id}/metadata | early local-admin ASGI | services |
| POST /api/v1/resources | early local-admin ASGI | services |
| POST /api/v1/resource/{id}/versions | early local-admin ASGI | services |
| POST /api/v1/resource-versions/{id}/process | early local-admin ASGI | services |
| POST /api/v1/processing-jobs/{job_id}/retry | early local-admin ASGI | services |
| POST /api/v1/master-data/sync | early local-admin ASGI | services |
| GET /api/v1/master-data/sync/status | early local-admin ASGI | services |
| PATCH /api/v1/master-data/sync/auto | early local-admin ASGI | services |
| GET /api/v1/master-data/sync/files | early local-admin ASGI | source_file_admin |
| POST /api/v1/master-data/sync/files/retry-failed | early local-admin ASGI | source_file_admin |
| POST /api/v1/master-data/sync/files/{file_id}/retry | early local-admin ASGI | source_file_admin |
| POST /admin/sync/master | early local-admin ASGI | services |
| POST /api/v1/resource-versions/{id}/index | early local-admin ASGI | services |
| POST /admin/resources/{id}/versions/ingest | early local-admin ASGI | services |
| GET /api/v1/resource-versions/{id}/chunks | early local-admin ASGI | search_access + Search.authorize_content_version |
| POST /admin/resource-versions/{id}/ocr-jobs | early local-admin ASGI | services |
| GET /api/v1/resource-versions/{id}/index-status | early local-admin ASGI | public-safe DTO |
| POST /admin/resource-versions/{id}/postprocess | early local-admin ASGI | services |
| POST /api/v1/resource-versions/{id}/reindex | early local-admin ASGI | services |
| GET /admin/resource-versions/{id}/etl-status | early local-admin ASGI | services |
| GET /api/v1/resource-versions/{id}/content-units | early local-admin ASGI | search_access + Search.authorize_content_version |
| GET /admin/resource-versions/{id}/content-units | early local-admin ASGI | services + Search.authorize_content_version |
| GET /openapi.json | public safe read | public-safe DTO |
| HEAD /openapi.json | public safe read | public-safe DTO |
| GET /docs | public safe read | public-safe DTO |
| HEAD /docs | public safe read | public-safe DTO |
| GET /docs/oauth2-redirect | public safe read | public-safe DTO |
| HEAD /docs/oauth2-redirect | public safe read | public-safe DTO |
| GET /redoc | public safe read | public-safe DTO |
| HEAD /redoc | public safe read | public-safe DTO |

No client-supplied actor/scope/header grants identity. Forwarded and non-loopback Vite proxy requests are rejected. Protected and API error responses are private, no-store. Bounded preview/file size checks do not replace authorization.

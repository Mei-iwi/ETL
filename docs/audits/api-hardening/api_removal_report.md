# G2 - API removal review

Status: **PASS (report only), NO_SAFE_REMOVAL**. 40 business method/path pairs KEEP, 0 REMOVED; 8 framework GET/HEAD methods KEEP. Registered OpenAPI remains 39 operations on 38 paths. This is a compatibility decision, not an assertion every endpoint has an external consumer.

Baseline 48 entries: 29 USED_FRONTEND, 9 USED_TEST_SCRIPT, 10 EXTERNALLY_EXPOSED_UNKNOWN (8 framework + 2 business). See api_inventory.json for every method/path/handler, dynamic URL call sites and shared service references.

| Candidate | Evidence / compatibility | Decision |
| --- | --- | --- |
| POST /admin/resources/{id}/versions/ingest | No proven HTTP client in current checkout; ingestion use case used by CLI/worker/tests. External consumers/deprecation unknown | KEEP; removal BLOCKED_CONTRACT pending owner approval |
| POST /admin/resource-versions/{id}/ocr-jobs | No proven HTTP client; OCR service/worker/processing workflow used | KEEP; removal BLOCKED_CONTRACT |
| POST /admin/sync/master hidden alias | Regression/security tests use it; delegates to canonical sync handler | KEEP; same G1 guard, not a security bypass |
| admin postprocess/etl-status/content-units | Tests/CLI shared workflows, backward compatibility not waived | KEEP |
| GET /health, capabilities; sync/import/index/search/evaluation/file | Operational or frontend feature owner exists | KEEP |
| FastAPI docs/OpenAPI/redoc | Framework contract/tooling, not an internal dead handler | KEEP |

All other routes classified KEEP individually in inventory. No handler/DTO/port/adapter/DI/client removed. No table/column/index/file/collection deleted. G4 may replace in-RAM lookup implementation with SQL while preserving routes; this is not API removal.

Source scan includes templates/dynamic URLs, registered router/OpenAPI and tests/scripts/backend application service references. Absence of a frontend literal is not proof of unused code. External contracts cannot be inferred from this repository alone.

Before/after route-set comparison is enforced by test_api_hardening_inventory.py; final OpenAPI snapshot and summary generated at G7. HTTP permission changes are documented separately from route removal. No auth guard, health or worker entry point removed merely to produce a deletion commit.

Validation: assembled Backend unit regression 982 PASS; isolated PG/Mongo regression 57 PASS and 1 historical XFAIL (0005); real pipeline/Evaluation/lineage diagnostics 26 PASS; Frontend 65 PASS, build PASS and lint 0 errors/5 baseline warnings. Ruff, static governance and diff checks PASS. These overlapping runs must not be summed as unique tests. Live OAuth, Dev writes and human Ground Truth were not run.

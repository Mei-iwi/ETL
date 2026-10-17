# API/dataflow hardening - final acceptance

Overall: **PARTIAL**. Independent code/test phases PASS; B04 lineage and B09 full-SOURCE catalog semantics remain unapproved. No live Dev/OAuth or human-validated search-quality acceptance is claimed.

## Scope and Git

- Branch: refactor/etl/api-contract-dataflow-hardening.
- Baseline: 1ba6b86 (PR60 / Prompt05 integrated); initial worktree clean, frontend in the same repository.
- Actual checkout: D:/StudyMaterials/HK7/Final_Project/ETL (the earlier HK7/Khóa luận cử nhân/ETL path no longer exists).
- Two pre-existing stashes retained. No push, merge, rebase, reset, stash/drop or archive changes.
- Only contract_snapshot.json and decisions.json were restored from 1135ccd, following explicit operator approval. Both Git blob hashes and normalized content match exactly; the Windows checkout applies CRLF (raw working-file bytes therefore differ only in line endings). No replacement baseline, schema approval or historical migration edit.
- Full changed-file list: `git diff --name-status 1ba6b86 HEAD`; individual files and evidence are in each phase document.

| Phase | Runtime changes / evidence | Result | Commit |
| --- | --- | --- | --- |
| G0 | API inventory, immutable baseline and exact approved restoration | PASS | 54a2730 |
| G1 | Verified local boundary, ownership before storage, sensitive text policy, proxy denial | PASS - local-only | f7cc0f7 |
| G2 | Compatibility report; NO_SAFE_REMOVAL | PASS - report only | 8673900 |
| G3 | Two PostgreSQL defect diagnostics; no runtime fix | BLOCKED B04 / NOT_FIXED | NOT_COMMITTED as G3 |
| G4 | SQL page/filter/count, 7-row server-paged UI, existing catalog semantics | PASS for approved independent scope; B09 pending | 91ed2f9 |
| G5 | Actual-byte preview budget, size/sniff checks, readiness retained | PASS isolated/fixture | b131f54 |
| G6 | Genuine PNG64 from existing AVIF artwork, subpath checks | PASS | 57976f2 |
| G7 | Regression, preview-proxy/legacy ownership closure, snapshots, diagnostics, README/template | PASS available verification; overall PARTIAL | Commit containing this report |

Resolve the G7 SHA with `git log -1 --format="%h %s" -- docs/audits/api-hardening/final_report.md`. A report cannot embed its own content-addressed commit hash; the final handoff provides the resolved SHA. G3 diagnostic evidence is included in G7, not represented as a lineage fix.

## API and security

- Registered: 48 method/path entries = 40 business + 8 framework GET/HEAD methods.
- OpenAPI: 39 business operations on 38 paths; one hidden compatibility alias is inventoried.
- KEEP 48; REMOVED 0; ADDED 0; DTO/path definitions unchanged.
- Removal BLOCKED_CONTRACT: legacy POST /admin/resources/{id}/versions/ingest and POST /admin/resource-versions/{id}/ocr-jobs. Repository absence of a HTTP caller does not disprove external consumers.
- No handler, shared worker/CLI service, schema, storage data or frontend feature was removed solely for lacking a frontend literal. In-RAM lookup helpers were replaced only as part of G4.
- [Inventory](api_inventory.md), [removal decisions](api_removal_report.md), [machine-readable diff](api_diff.json), [before](openapi_before.json), [after](openapi_after.json), [post-change security matrix](security_after.md).
- Production administration is default-deny. Development requires enabled admin, verified Uvicorn loopback/no-proxy launch, actual loopback peer/server/Host/Origin and no forwarded headers. Vite dev AND preview proxy reject remote socket peers. Final verification found preview inherits server.proxy in the installed Vite source; the same guard was wired to configurePreviewServer and regression tested, without new architecture or routes.
- Early ASGI checks precede multipart/body consumption. SOURCE/UNKNOWN manual version writes fail before storage; LOCAL is rechecked under the existing write lock. Client actor fields cannot grant identity.
- Final alias review also applied the canonical LOCAL policy to legacy HTTP ingest, before storage.snapshot and again under the write lock. It cannot bypass SOURCE/UNKNOWN ownership; missing resource is 404, ownership conflict 409. The optional internal local_only flag is set by the server route, never accepted from request JSON. Trusted CLI/orchestrator calls retain their previous behavior; B04 import semantics are not silently changed.
- Chunk/ContentUnit reads apply Search content rights, including restricted/REJECTED ancestry. Pre-index processing preview remains usable under those rights; whole-file viewer still requires CONTENT_READY and INDEX READY.
- Protected/API responses are private, no-store. Exceptions do not expose storage paths, private adapter text or submitted secrets.
- This is trusted-local operation, NOT a Production identity provider/RBAC. Public full-text rights were not inferred from PUBLISHED.

## Dataflow and retained decisions

- SOURCE15 / Master18 / Mongo5 unchanged; six Mongo business indexes and ContentBag 384D contract unchanged.
- Static governance PASS: drift=[], SOURCE head s0003, Master head 0015, live_applied=false. This is code parity, not an inspection of live Dev schema.
- B03/B04/B07/B08/B09 remain pending in the exact historical register. No DDL, trigger/migration/ORM/schema changes or .env edits.
- Historical 0005 downgrade index-name mismatch is KNOWN_DEFECT / NOT_FIXED; isolated expected failure is not counted as PASS.
- [B04 evidence and proposal](lineage_decision.md): SOURCE V1 -> (V2,V3) can still become a linear target chain; import 1/3/2 can reject 2. Operator approval is required before changing parent semantics or repairing historical lineage.
- LOV remains USED_ONLY_MASTER. Canonical active identity, deduplication, Unicode casefold, stable sorting and response fields preserved. One SQL statement provides count/page snapshot; 1,100 synthetic entries tested. No new catalog tables. B09 ALL_SOURCE needs a separate decision.
- Viewer: denied 403; hidden/missing 404; not-ready 409; oversized 413; service failure 502; authorized 200/Range206. Actual preview-byte budget 30 MiB (Office text 1 MiB), abort/remount/revoke on resource changes. No remote Office/Drive viewer.
- Source logoBook.png is AVIF 626x626; generated favicon.png is real RGBA PNG64. Source unchanged, existing Pillow used, no dependency added. Dev and build/preview subpath return HTTP200 image/png.

## Test environment and commands

Interpreter: repository .venv, Python 3.13.0; pytest 9.1.1; FastAPI 0.142.2; SQLAlchemy 2.0.54; PyMongo 4.18.3; Pillow 12.3.0; Torch 2.14.1; sentence-transformers 5.7.0. Node 22.17.1 with --experimental-strip-types; Vite 8.3.3; actual headless Microsoft Edge for browser checks.

Real embedding: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42, local Models/paraphrase-multilingual-MiniLM-L12-v2, 384 finite dimensions. Offline Hugging Face; no download or credential use. Real Tesseract, eng+vie.

Safety harness: Settings.env_file=None, explicit temporary TEST_* URLs, no .env reads; unit harness disallows external sockets. Integration uses newly created labelled postgres:16 and mongo:8.0 containers, random loopback ports and tmpfs only. Cleanup validates exact owned IDs/labels and removes only those containers, never Dev volumes. Each test uses isolated schemas/databases, not etl_content Dev.

Underlying commands (execute only with the isolated configuration above, never point TEST_* at Dev):
```text
python -m pytest -p no:cacheprovider -q --tb=short tests/unit
python -m pytest -p no:cacheprovider -q --tb=short tests/integration/test_catalog_pagination.py
python -m pytest -p no:cacheprovider -q --tb=short tests/integration --ignore=tests/integration/test_google_drive_readonly.py --ignore=tests/integration/test_cli_stage_flow.py -k "not react_ui"
python -m ruff check --no-cache .
python scripts/check_migration_governance.py
node --experimental-strip-types --test frontend/tests/*.test.mjs
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
git diff --check
```

Definitive full unit run on final code/fixtures: **987 PASS**, 457.82 s, no skips/XFAIL/failures. Earlier checkpoint: 982 PASS, 521.24 s; focused changed-code/OpenAPI regression: 130 PASS, 94.85 s. Final legacy HTTP ownership cases also passed independently (4 PASS, 4.56 s). These overlap the definitive full run; do not sum them as unique tests.
Frontend final rerun: **66 PASS**, 18.02 s, 0 skip/fail; actual Edge fixture UI covers Master paging/filter/read-only, File readiness/errors desktop/mobile, ownership, Index and Search. This supersedes the earlier 65-test run and includes both proxy-hook checks. Build PASS; lint 0 errors and 5 unchanged set-state-in-effect warnings (SystemStatus, SystemCapalities, ResourceDetail, VersionHistory, UpdateVersion). Node Type Stripping emits its expected experimental warning, not an application lint warning.
Ruff --no-cache, static governance and diff checks PASS. Audit rerun via python -m scripts.audit_api_hardening confirms all four G0 artifacts remain unchanged; the generator no longer overwrites frozen usage/security snapshots. Python mypy/pyright is not configured, so no separate Python type-checker PASS is claimed.

Earlier independent isolated batches: 57 PASS + 1 historical XFAIL (59.38 s); real format/Evaluation/lineage batch 26 PASS (151.22 s); final PostgreSQL Unicode/page tests 2 PASS (2.28 s).
Broad final isolated integration selection on the final legacy-ownership code: **203 PASS, 1 historical XFAIL, 3 deselected**, 430.90 s, 0 failures/skips. The three deselected cases are existing opt-in real-model Index UI variants; no new test is skipped/XFAIL. Live Drive and CLI subprocess modules were explicitly excluded for credential/dotenv safety, not reported as PASS. Four existing embedding adapter FutureWarnings concern a renamed model-dimension method; no warning filter was added.

The first broad run was 194 PASS / 9 FAIL / 1 XFAIL. Failures were fixture compatibility: three local reads lacked explicit admin enablement, three real-model manual API variants used the old untrusted client and stale Search adapter, and three Mongo projection tests omitted the now-required dimension argument. Only those fixtures were corrected, preserving negative tests, runtime guards, schema and data contracts; the entire selection then reran 203 PASS.
The corrected pre-alias checkpoint had also passed 203 tests in 322.69 s; the final rerun above verifies the additional HTTP-only ownership flag does not change trusted importer/worker behavior. These are repeat runs, not additional unique tests.

## Real pipeline versus mocks and quality

- 10 format cases (native PDF, scan PDF, DOCX, XLSX, PPTX, TXT, CSV, PNG, JPG, JPEG) x Local and Drive-mock transport. Real isolated PostgreSQL/Mongo, file checksum, Tesseract and model; verified CONTENT_READY -> INDEX_BUILD -> ContentBag/384D -> READY, Index/Reindex, Search and Context.
- Drive HTTP transport is mocked, not live OAuth. Source file bytes/hash are preserved.
- Evaluation: real model, 18 query templates x BoW/Vector/Hybrid = 54 observations, k=1,3,5, all three methods complete 18/0 in the isolated successful run. Label provenance unconfirmed; QUALITY_NOT_RUN, no comparative search-quality claim.
- Inference outage was explicitly injected in a separate test: BoW 18 successes, Vector/Hybrid 0 successes with BLOCKED_EMBEDDING_RUNTIME; recovery verified. This is simulated outage, not an observed persistent runtime defect.
- Evaluation browser test uses real Uvicorn + isolated SQLite/typed Mongo doubles + real embedding, not Dev PG/Mongo. The four existing routes, progress, refresh and CSV BOM work.
- The 18-query preparation fixture contains placeholder resource IDs and null relevance, ground_truth_confirmed=false. It cannot be imported as confirmed labels until a human selects IDs and supplies grades.
- Initial broad unit failures from legacy trusted-client fixtures were fixed explicitly, without enabling global admin or weakening negative tests. One later unit failure was the safety harness incorrectly denying its own fake .env packaging fixture; the harness was corrected to allow only its own temporary fixture directory, then all 982 tests reran PASS.
- PostgreSQL casefold initially failed with compiler RecursionError; a flat SQL expression node fixed it, no schema function or database write.
- The new legacy HTTP test initially attempted source_origin=NULL, which the schema correctly forbids. That invalid fixture was replaced by a real missing-resource 404 case; all four SOURCE/UNKNOWN/missing/LOCAL cases passed. No nullability/schema change was made. An obsolete unit run that had already collected the invalid fixture was stopped only after verifying its exact repository interpreter, parent harness and child PID; Backend and integration were untouched.

## DONE / PARTIAL / NOT_RUN / BLOCKED

DONE: independent G0/G1/G2/G4/G5/G6 implementation/reporting and available isolated/offline verification; original schema contracts/restored decisions retained; per-PASS-phase commits; no push.

PARTIAL: overall acceptance; trusted-local security is not multi-user authentication, and fixture browser UI is not live Dev end-to-end.

NOT_RUN: actual OAuth Drive access; live Dev data/schema writes; full Dev browser/corpus acceptance; the three existing opt-in real-model Index UI variants; CLI subprocess acceptance (may read repository dotenv); human-confirmed Ground Truth quality; Production authentication/access testing.

BLOCKED: B04 lineage policy/repair; B09 full catalog semantics; B03/B07/B08 schema decisions; external legacy-route removal approval. No new database migration is required for completed changes.

Next: review commits; approve lineage/missing-parent behavior and catalog scope separately if desired. Arrange read-only OAuth acceptance and human relevance review with explicit environment authorization. Do not backfill/reindex/alter Dev data merely to clear these statuses.

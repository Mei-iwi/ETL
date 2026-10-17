# Search runtime reliability - scoped acceptance

Date2026-10-18. Base058bd6f5d45ed33126e9c7d9bd5676f97c03f87e, branch perf/etl/search-evaluation-model-runtime.
Status: PASS for the scoped isolated Search acceptance. All scoped gates passed; one requested commit only, no push/merge. Production READY is not claimed.

## Confirmed Search defects and fixes

- frontend/src/services/searchApi.ts (original22/33, now32/42/67-108): HTTP200 JSONnull/arrays/error objects/missing fields/wrong nested types were cast to valid responses. SearchResultPage's falsy-data branch left loading/aria-busy indefinitely. Added essential runtime Search/Context validation, request/query/method/page binding, finite scores, typed arrays, optional-field compatibility and explicit safe SEARCH_INVALID_RESPONSE. Unknown additive metadata remains accepted; API response schema unchanged.
- Same service: raw backend detail.message and network Error.message could reach UI. Only local allowlisted messages are displayed now, with safe status fallbacks. Explicit abort remains AbortError; deadlines and failed body streams are distinguished from malformed JSON and network failures; aborted body success is not published.
- frontend/src/pages/search/SearchResultPage.tsx:28: display only safe SearchApiError messages, generic text for unexpected exceptions. Existing StrictMode microtask, abort guard, submit/retry refs and finally cleanup are retained; no layout/CSS change.
- application/search.py:51 and api/routes.py:162-167: cooperative timeout checks run after synchronous model work; concurrent Search requests could wait behind a blocked model. Added one nonblocking HTTP Vector/Hybrid slot per SearchService/process, after server-derived SearchAccess. Busy requests get existing503 error shape with SEARCH_RUNTIME_BUSY. No waiting queue, no model reset or new worker. Slot is held until actual completion and released on success/error. BoW and public fail-closed responses bypass it; health and BoW remain usable. Direct service consumers (including Evaluation) do not use this HTTP guard.

No model adapter, prepare/encode algorithm, cache, ranker, score formula, normalization, permissions, filters or pagination changes. QueryEmbeddingContext untouched. Actual path remains Frontend service -> Search route/access -> SearchService -> same embedding and UoW/Mongo candidates -> same guards/ranking -> response. One Container/LocalSentenceEmbedding per process as before; no startup preload/download added.

## Regression before / after

Tests were added before fixes: new frontend31tests initially1PASS/30FAIL (invalid success, exposed messages and network errors), then31PASS. Two additional body-read cases initially2FAIL, thenPASS after the targeted abort/transport correction. Backend admission test initiallyFAILED because no guard existed. No assertion was removed to obtain PASS.

Existing Search mock success payloads omitted mandatory API fields; they now use complete wire contracts in searchResponses.mjs. Browser fixtures likewise include the existing backend-required representation/count/item metadata. This does not loosen validation or change the actual API. All original browser assertions remain, with new malformed/null/array/missing/network/retry checks.

| Gate | Final current result |
|---|---|
| Backend full unit |1052PASS/3SKIP in474.93s; no FAIL. Three real OCR image cases use default PATH, left unchanged; outside Search|
| Focused Search/access/Context/runtime |105PASS/43.14s; included in full unit, not additive|
| Final admission/BoW/health recovery |2PASS/1.65s; included in full unit|
| Search integration (real PG/Mongo/local384D + SQLite fixture) |3PASS/25.42s,2existing warnings; no SKIP|
| Full Frontend |103PASS/18.66s,0FAIL/SKIP|
| Search-only real-model paired benchmark |1PASS/39.04s,1existing warning|
| Build/lint |PASS/0errors;5existing set-state-in-effect warnings|
| Ruff / git diff --check / governance |PASS;15SOURCE/18Master/5Mongo,drift=[]|

Full unit/focused runs use scripts/run_b04_b09_tests.py with outer dotenv/network guard and temporary fixtures. Integration runs only tests/integration/test_search_runtime_pg.py and test_search_integration.py with explicit offline model configuration. Real PG16/Mongo8 were fresh owner-labeled tmpfs containers, random loopback ports61461/61462 for the first run and63456/63457 for the final burst test run; only the runner-owned containers were removed. No connections, DDL, indexing/OCR/worker or dataset operation against Dev. Existing indexing/OCR helpers are used ONLY to construct READY isolated test fixtures, never to extend those features or operate Dev.

Final physical-store integration verifies:
- All3methods return valid results and Context; no embedding exposed in JSON.
- Exactly one prepare and one encode for each Vector/Hybrid call, none for BoW.
- The same loaded real model instance is retained.
- One controlled in-flight real-model Search plus8competing HTTP requests: all8return503SEARCH_RUNTIME_BUSY without prepare/encode; BoW still200; first request completes successfully; model object unchanged.
- Revocation in the temporary PG fixture removes all3method results and makes old Context404; Mongo documents unchanged by Search.

Browser Edge uses dedicated profile/local mock HTTP, not Dev E2E. It verifies no duplicate POST under StrictMode/rapid Enter, old delayed response cannot overwrite new route/results, malformed responses finish loading with an actionable retry, and valid retry succeeds. Node covers real AbortSignal deadline, caller abort, body-phase abort/network disconnect and all401/403/404/409/422/502/503 safe errors. No backend cancellation claim is made when the user leaves the page.

## Before/after Search-only measurement

paired-search-real.json compares frozen baseline058bd6f SearchService against current SearchService on the SAME SQLite/typed Bag corpus (6versions/13bags/18synthetic queries), same loaded CPU model/revision, alternating3repetitions,54samples/method/side. Full response including IDs/order/scores/context/source attribution matches exactly,tolerance0; access tests independently PASS. No Evaluation call is used for this benchmark.

| Search | p50 before ms | after | p95 before ms | after |
|---|---:|---:|---:|---:|
|BoW|35.90|42.18|67.30|64.32|
|Vector|70.29|68.91|100.57|98.70|
|Hybrid|70.37|71.21|94.75|102.84|

Independent Vector/Hybrid still encode each request:108before/108after, one SentenceTransformer load. No redundant prepare added. Model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42,384D,CPU threads2,offline. Cold imports+weights+probe14.64s in this process. Adapter unchanged; cold measurement is a single uncontrolled OS-cache sample, NOT proof of a cold-start improvement. Warm measurements concern service work, not HTTP admission overhead or physical database latency. Shared-machine jitter and mixed p50/p95 movements do not establish speedup/regression; no ranking was changed for timing.

## Limits and preservation

- Synchronous inference cannot safely be killed here. A hung admitted Search keeps ONE model HTTP slot occupied until it returns; subsequent Vector/Hybrid Search fails fast rather than queues. Client timeout/abort only ends waiting. No claim of hard inference deadline, global model concurrency control, or memory-leak-free Production capacity.
- Per-process limit means each API worker has its own model/slot. This guard does not govern direct internal consumers or other workers; no global model architecture change.
- Real Production auth/live corpus and prolonged memory/traffic soak NOT_RUN. Current public policy remains fail-closed, local_demo remains Development-only; not Production READY.
- Evaluation lock recovery and intermittent Reindex UI acceptance are outside this task and not fixed/rerun. Code-derived snapshot/fingerprint protections remain intact, not bypassed or updated in stored data.
- SOURCE15/Master18/Mongo5, migration history, .env, credentials, models and Dev data unchanged. No dependencies/configuration added.
- Initial untracked docs.rar and previous post-optimization-review artifacts, plus both stashes, are preserved and excluded from the commit. Only the Search changes/tests and this scoped report/measurement belong in the requested commit.

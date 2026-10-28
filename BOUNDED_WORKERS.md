# Bounded multi-worker ETL — implementation and operating report

## A. Repository baseline

Baseline: `main`, `9af41d889453f924bc3675c905f41498f2cd293c`, merged [PR #111](https://github.com/Mei-iwi/ETL/pull/111).
GitHub main was checked and pointed to the same SHA. Work is isolated in
`.worktrees/bounded-parallel-workers`, branch `feat/sync/bounded-parallel-workers`.
The original checkout had untracked `.worktrees/`; its tracked files and `.env` are untouched.
No push, merge, rebase, cherry-pick, application data migration or SOURCE write was performed.
Migration heads are MASTER `0019` and SOURCE `s0003`; tests migrate only disposable schemas
in the separately created local database `etl_parallel_test`.

| Component | Classification | Decision |
|---|---|---|
| MasterSync / AutoSyncCoordinator / master worker | KEEP_UNCHANGED | Global sync lock, stream order, baseline FULL, replay window, committed checkpoint semantics |
| ReconciledSync / ReconcileMaster | KEEP_UNCHANGED | Exact-ID repair, source ownership, auth freshness, deletion safeguards |
| Source import queue, source import worker | EXISTING_REUSE | SKIP LOCKED, tokens, lease, heartbeat, retry and crash recovery |
| Source import claim | OPTIMIZE | Database capacity and resource/version admission before attempts increment |
| Source discovery | EXISTING_REUSE | Existing cross-process discovery lock and persisted keyset; polling still checks each process |
| Source resolver / temporary files | KEEP_UNCHANGED | Read-only SOURCE, byte/hash/policy verification, attempt directories and OS locks |
| AuditQueue | OPTIMIZE | Serialize admission; per-stream exclusion and global active capacity |
| ScheduledAudit / AuditSchedule | EXISTING_REUSE | Persisted scheduler, deduplication, lease fencing and checkpoint resume |
| ReconciliationEngine / file audit | KEEP_UNCHANGED | LIGHT/DEEP, SHA-256 evidence, NOT_VERIFIED, byte/time budgets, sequential hash loop |
| OCR queue / OCR worker | EXISTING_REUSE | Per-page claims, recovery, counters, finalizer callback |
| OCR ownership | OPTIMIZE | Reject expired heartbeat even before stale recovery replaces the token |
| Processing job creation/retry | OPTIMIZE | Optional internal fence in the transaction that writes the job/tasks |
| Processing finalizer / Mongo projection | EXISTING_REUSE | Version lock, deterministic IDs, projection generations and version-scoped reconciliation |
| processing_lock | OPTIMIZE | Same PostgreSQL advisory key with session ownership on AUTOCOMMIT connection |
| SQLAlchemy sessions / pools | KEEP_UNCHANGED | Separate session per UoW; no shared thread session; pool defaults remain per process |
| Config / portable Compose | ADD | Two validated capacity settings, default 1, maximum 2; expose existing per-container budgets |
| Frontend / HTTP contracts / business schema | KEEP_UNCHANGED | No changes |
| Optional parallel file hashing / thread executor | KEEP_UNCHANGED | Deferred pending end-to-end benchmark evidence |
| Adaptive resource control | ADD | Manual bounded admission only; automatic CPU/RAM controller is deferred |

## B. Architecture after implementation

The business path remains SOURCE → Reconciliation/Sync → MASTER → Source File Import
→ Processing/OCR → Metadata/Content Index → Search.

```mermaid
flowchart LR
  S[SOURCE read only] --> M[Master Sync: one logical owner]
  S --> A[Audit workers: 1 by default, at most 2 active]
  A -->|explicit exact-ID repair through sync_lock| M
  M --> Q[(Existing source_file_imports)]
  Q --> F[File workers: 1 by default, at most 2 active]
  F --> O[(Existing OCR tasks)]
  O --> W[OCR processes: 1; optionally 2 within RAM budget]
  W --> P[Existing version finalizer and projection]
  P --> I[Existing metadata/content index and search]
```

Admission takes a short PostgreSQL transaction advisory lock for the queue, reads
live lease count and resource/stream ownership, then claims with SKIP LOCKED and
increments attempts in that same transaction. The lock is released before execution.
This closes the race that a standalone NOT EXISTS predicate would leave. SQLite
reserves its single writer with BEGIN IMMEDIATE; PostgreSQL process tests are the
concurrency authority.

An audit stream has at most one unexpired RUNNING claim, including LIGHT/DEEP/IDS/
METADATA variants. A live execution lock also prevents claiming/reclaiming that
stream without spending a retry. Different streams may execute concurrently.
Global audit capacity counts claims, including repair audits requested by Master Sync;
idle processes do not consume it. Expired claims can recover once the previous
execution lock is gone. Transaction probes use the existing processing key namespace
and are nonblocking, preventing a reverse lock-order wait.

Source admission allows independent resources. A live FETCHING claim blocks other
versions of that resource. An older PENDING/FETCHING/IMPORTED/RETRY_PROCESSING/
RETRY_WAIT mapping without a committed target blocks newer claims, including while backoff is pending. Expired
FETCHING is reclaimed before later versions. Terminal FAILED/RETRY_EXHAUSTED does
not hide the resource forever: the existing parent validator reports blocked lineage
and the existing manual retry path remains available.
For a committed parent mapping in RETRY_WAIT, preserve the existing child claim and
`_parent` validation path: it rejects with PARENT_PENDING before publishing a descendant.
An earlier broad regression caught this distinction; the final source verification
retains the original lineage test unchanged.

PROCESSING is **not** content completion. It can permit the next import because
the target mapping is committed, OCR jobs reference distinct target version IDs,
the finalizer locks a version, Mongo page IDs include the job, content-unit IDs are
distinct, and reconciliation/invalidation selectors include resource_version_id
and generation. METADATA_ONLY also permits a later mapping; it does not promise
searchable content. Parent identity, policy and lineage checks remain mandatory.

Import fencing now also surrounds OCR job creation/retry writes in their own
transaction. A rejected fence rolls back tasks, job counters/options and retry
stages atomically. OCR ownership requires a matching token, RUNNING state and a
fresh heartbeat. Existing recover_stale handles a worker that cannot renew.

PostgreSQL processing locks now use a dedicated AUTOCOMMIT connection and a session
advisory lock. They still consume a pooled connection, but no transaction remains
open while hashing or projecting to MongoDB. The global MasterSync lock is unchanged.

## C. Code changes and risk

| File | Change | Reason | Risk |
|---|---|---|---|
| `adapters/persistence/queue_admission.py` | Short database admission lock and execution-lock probe | Serialize decision + claim across processes | Brief queue claim serialization |
| `adapters/persistence/sqlalchemy.py` | Source capacity/dependency guards; AUTOCOMMIT processing lock | Protect resource versions and avoid idle transactions | Admission queries add work; one held connection per processing lock |
| `adapters/persistence/audit_queue.py` | Capacity + same-stream admission before attempt increment | Busy stream must not consume retry | All callers must run upgraded code |
| `application/source_file_import.py`, `ports/repositories.py` | Pass configured capacity; pass import fence to processing | Preserve existing queue and execution path | New optional internal parameters |
| `application/ocr.py` | Heartbeat expiry check and creation fence | Reject stale writes, including before token replacement | Previously accepted late results are rejected and recovered |
| `application/process_version.py`, `application/retry_processing_job.py` | Carry optional transactional fence | Close lease expiry gap before job writes | Requires coordinated rollout with import code |
| `config.py`, `.env.example`, `deploy/docker.env.example` | Validated global capacity 1..2 | Backward-compatible defaults | Mixed settings can reduce effective capacity |
| `deploy/compose.app.yml` | Pass capacity and existing resource budget settings | Enable explicit scaling without changing replica defaults | Per-container budget must be multiplied by replicas |
| `tests/unit/test_bounded_workers.py`, `tests/integration/test_bounded_*.py` | Unit, PostgreSQL process, crash, fencing, PDF/Mongo tests | Demonstrate actual overlap, exclusion and durable state | Synthetic queue tests do not prove end-to-end performance |
| `scripts/benchmark_bounded_workers.py` | Reproducible isolated queue benchmark | Record measurements without application writes | Synthetic workload; not a sizing recommendation |
| `scripts/worker_queue_status.py`, `scripts/process_metrics.py` | Read-only queue snapshot and native Windows process sampling | Inspect backlog/leases/DB waits and measure benchmark CPU/RSS without a new dependency | Snapshot metrics do not replace a persistent fleet collector |

Paths in this table are relative to `src/data_sync_etl` unless explicitly rooted.

## D. Test evidence

Exact counts, failure IDs, skips and baseline comparison are recorded in
`BOUNDED_WORKERS_TEST_RESULTS.json`. JUnit evidence is retained under
`artifacts/bounded-workers/` locally. Historical `RECONCILIATION_TEST_RESULTS.json`
was read before testing; it is not treated as proof that the current baseline passes.

| Test group | Passed | Failed | Skipped | Errors | Status |
|---|---:|---:|---:|---:|---|
| Clean baseline full unit | 2,209 | 7 | 9 | 0 | Baseline failures retained |
| Broad unit snapshot before final guard correction | 2,218 | 8 | 9 | 0 | Seven baseline failures plus one subsequently corrected lineage regression |
| Expanded affected snapshot before correction | 667 | 4 | 1 | 0 | Three baseline failures plus that corrected regression |
| Latest source/lineage/processing verification after query aggregation | 80 | 0 | 1 | 0 | PASS for executed tests; symlink environment skip |
| Latest PostgreSQL bounded worker/pipeline tests | 34 | 0 | 0 | 0 | PASS on current admission query |
| PostgreSQL lock/fencing/base regression run | 48 | 0 | 0 | 0 | PASS; overlaps the current worker tests |
| Full integration snapshot | 222 | 40 | 85 + 1 xfail | 4 | PARTIAL; not a passing suite |
| Additional baseline integration failure confirmation | 0 | 19 | 0 | 0 | All 19 reproduced on clean HEAD |
| Portable runtime/lifecycle tests (included in focused run) | 35 | 0 | 0 | 0 | PASS |

There are **55 distinct passing PostgreSQL test IDs** across focused runs; overlapping
runs must not be added together. All 40 integration assertion failures have baseline
evidence: 21 in the previous report and the remaining 19 reproduced on an extracted
clean HEAD in this session. The integration collection error is also confirmed on
the unchanged checkout. Three setup errors with SyncAlreadyRunning remain unclassified
and appear sensitive to test order/global SQLite locks; they are not claimed as fixed
or proven unrelated. The broad suites began before the final handoff fences and
admission correction; the final affected scope was reverified separately.

The process test handles 100 synthetic independent import claims with unique tokens
and exactly one terminal queue output per row. Other tests race two independent
audit processes, assert zero attempts for denied work, kill an importing process,
recover after actual lease expiry, fence the old token, verify real execution overlap
and compare cross-stream summaries with serial execution. PostgreSQL tests also
exercise the source pipeline's hash/policy checks, version identity and recovery.

The PDF/Mongo test uses two separate worker/container compositions, separate UoW
sessions, two native-text PDFs, real PostgreSQL results and an isolated real MongoDB
database. It checks six unique page results, correct counters, two successful
finalizations and matching content-unit counts. It does not exercise Tesseract on
scanned pages or run production embedding models. Existing indexing/search and auth
regressions are reported separately, including failures and environment-dependent skips.

The unchanged baseline has an integration collection error: `test_source_lineage_pg.py`
imports `test_source_parent_changes_during_copy_rolls_back_mapping`, while the unit
module defines `test_source_parent_changes_during_verification_rolls_back_mapping`.
The error remains reported. `--continue-on-collection-errors` executes collectable
tests while retaining a nonzero exit status; it does not make this a passing suite.

Fix rounds: first run found an incorrect new test call to `repo.count` without filters;
second run passed the focused groups. Final checks include the additional transactional
fences and AUTOCOMMIT lock checks. Baseline failures are not deleted or weakened.
The expanded run caught one real new regression in committed-parent RETRY_WAIT handling;
the original lineage test was preserved and passes in the final source and PostgreSQL runs.

## E. Benchmark scope

`BOUNDED_WORKERS_BENCHMARK.json` records measured queue-only results for 100, 1,000
and 10,000 synthetic import rows at one and two worker processes. It includes commit,
dirty-tree status, runtime/platform, latency, SQL count, throughput and sampled CPU/RSS.
SOURCE bytes and queries are zero because this workload performs no file download.
Checkpoint correctness is explicitly NOT_EXERCISED_QUEUE_ONLY. The diagnostic run
shares the host with regression tests; the recorded environment note prevents treating
these measurements as a controlled performance comparison.

This is not the requested full comparison of 2 File + 2 Audit workers against one of
each with mixed/large files and metadata discrepancies. No end-to-end speedup is
claimed. Four-worker runs are not enabled because admission is capped at two and
there is no resource/benefit evidence justifying four. Default and recommended rollout
remain one worker per role until representative end-to-end benchmarks pass.
The first 10,000-row diagnostic exceeded its 300-second worker-result timeout. A
performance-stage change replaces repeated correlated predecessor checks with an
aggregate of the first waiting version per resource and a bounded live-resource
selector, retaining the same admission rules. The 80 source and 34 PostgreSQL tests
were rerun successfully after this change. No migration/index or hash executor was added.

Measured diagnostic results (single sample, process startup included):

| Jobs | Workers | Jobs/s | Duration (s) | CPU mean (%) | RAM peak (MiB) | Retry |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 1 | 20.03 | 4.99 | 49.5 | 313.7 | 0 |
| 100 | 2 | 28.97 | 3.45 | 117.1 | 471.0 | 0 |
| 1,000 | 1 | 48.20 | 20.75 | 40.1 | 326.1 | 0 |
| 1,000 | 2 | 86.28 | 11.59 | 85.7 | 484.3 | 0 |
| 10,000 | 1 | 36.10 | 277.00 | 24.2 | 377.0 | 0 |
| 10,000 | 2 | 50.52 | 197.93 | 38.7 | 592.3 | 0 |

CPU is the sum for benchmark parent/worker processes, expressed relative to one
CPU core; it excludes the PostgreSQL server. RAM is their combined working set.
Every measured case had zero duplicate/error/retry output. At 10,000 jobs, two
processes used more peak RAM (592.3 vs 377.0 MiB) and had higher P95 claim/commit
latency (59.84 vs 40.10 ms), despite higher observed queue throughput. These are
diagnostic observations, not evidence for scaling production file/OCR pipelines.

## F. Operation and rollback

Use one consistent code version and settings for API, Master Sync, Source File and
Audit processes. Stop old workers before introducing upgraded claims; old binaries
do not know the new admission guard. Do not combine this checkout with other worktrees'
workers against the same queues. No schema migration is required for these changes.

Windows + Git Bash, from the checkout containing this code:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e '.[dev]'
# Configure .env using .env.example and your existing approved connection settings.
# Keep SOURCE_REFERENCE_ENABLED / SOURCE_TEMPORARY_FILES_ENABLED under existing policy.
```

Run each command in a separate activated terminal. Default capacity is one:

```bash
python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1
python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1
python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1
python -m data_sync_etl.workers.reconciliation
```

For two file/audit workers, set `SOURCE_FILE_IMPORT_MAX_ACTIVE=2` and
`RECONCILIATION_MAX_ACTIVE=2` in the shared `.env`, restart every relevant process,
then open two additional terminals:

```bash
python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-2
RECONCILIATION_WORKER_HEARTBEAT_FILE=D:/etl/private/audit-2.json python -m data_sync_etl.workers.reconciliation
```

If local heartbeat paths are configured, give **each** process its own file. Configure
the first audit process with `audit-1.json` and the second with `audit-2.json`.
OCR may use `--worker-id ocr-2` only when two loaded models fit RAM. Keep Master Sync
at one. IDs must be unique, at most 100 characters. Ctrl+C stops further claims and
lets current work finish; a forced kill relies on existing lease/stale recovery.
The existing SOURCE worker API heartbeat remains an aggregate last-writer marker;
it is not a per-worker health registry and may briefly appear unhealthy when its last
writer stops. Container lifecycle heartbeat files remain independent and are the
per-replica healthcheck authority.

Portable Docker (Git Bash): reuse the **same APP array and auth/Drive/Neon/remote/media
overlays selected in README**. For the local database + standard auth profile:

```bash
APP=(docker compose --env-file docker.env -f deploy/compose.app.yml -f deploy/compose.auth.yml)
"${APP[@]}" --profile workers config --quiet
"${APP[@]}" build api
# After editing docker.env consistently to capacity 2, recreate API/workers:
"${APP[@]}" --profile workers up -d --no-build --scale master-sync-worker=1 --scale source-file-worker=2 --scale reconciliation-worker=2 --scale ocr-worker=1 api master-sync-worker source-file-worker reconciliation-worker ocr-worker
"${APP[@]}" --profile workers ps
"${APP[@]}" --profile workers logs -f --tail 100 source-file-worker reconciliation-worker ocr-worker
"${APP[@]}" --profile workers stop -t 420 source-file-worker reconciliation-worker ocr-worker master-sync-worker
"${APP[@]}" --profile workers restart source-file-worker reconciliation-worker ocr-worker master-sync-worker
```

PowerShell equivalent for an already configured local/auth stack:

```powershell
$appArgs = @('--env-file','docker.env','-f','deploy/compose.app.yml','-f','deploy/compose.auth.yml','--profile','workers')
docker compose @appArgs ps
docker compose @appArgs up -d --no-build --scale source-file-worker=2 --scale reconciliation-worker=2 --scale ocr-worker=1 --scale master-sync-worker=1 api source-file-worker reconciliation-worker ocr-worker master-sync-worker
docker compose @appArgs stop -t 420 source-file-worker reconciliation-worker ocr-worker master-sync-worker
```

Worker services already have no fixed container_name or host port bindings. Docker
identities include hostname. `/run/etl` is separate tmpfs for each replica, so heartbeat
files do not collide. SOURCE mounts remain read-only. The root `docker-compose.yml`
provisions databases/migrations; worker scaling belongs to `deploy/compose.app.yml`.

Monitor queues using a read-only SQL connection to MASTER:

```sql
SELECT status, count(*), min(created_at) AS oldest FROM source_file_imports GROUP BY status;
SELECT stream_name, status, count(*), max(attempts) AS max_attempts
  FROM reconciliation_audit_jobs GROUP BY stream_name, status;
SELECT stream_name, count(*) FROM reconciliation_audit_jobs
  WHERE status='RUNNING' AND lease_expires_at > now() GROUP BY stream_name;
SELECT status, count(*) FROM ocr_page_tasks GROUP BY status;
SELECT count(*) AS expired_audits FROM reconciliation_audit_jobs
  WHERE status='RUNNING' AND lease_expires_at <= now();
SELECT state, wait_event_type, wait_event, count(*)
  FROM pg_stat_activity WHERE datname=current_database() GROUP BY state, wait_event_type, wait_event;
```

Use existing reconciliation APIs/UI for job checkpoint, queue_wait_seconds, execution
time, bytes_read and verification evidence; `GET /health` checks API readiness. Do not
query/export password material. SQLAlchemy's default QueuePool is per engine/process
(size 5 plus overflow 10); additional processes multiply PostgreSQL pools and Mongo
client pools. A processing advisory owner retains a connection even with no open
transaction. Measure `pg_stat_activity` and pool/lock waits before scaling.
`python -m scripts.worker_queue_status` provides a read-only JSON snapshot of queue
statuses, oldest creation timestamps, active/expired ownership, recent audit finishes,
PostgreSQL connection/wait states, and optional machine CPU/RAM metrics. It does not
export DSNs or document content. It is a snapshot, not a persistent metrics collector.

`SOURCE_TEMP_MAX_CONCURRENCY` and media/model limits are **per container** when temp
roots are separate. They are only cross-process on one shared local filesystem/root.
No global byte budget or fleet-wide download semaphore is claimed. Each file audit
keeps its existing per-job byte/time budgets. Deploy at most two replicas per scaled
role and account for API downloads too. If a hard fleet-wide download cap becomes
necessary, design a distributed leased permit or a private, correctly owned shared
filesystem with proven OS lock semantics first; do not assume container-local locks
provide that cap. Do not share heartbeat directories just to share download permits.

For overload, reduce admission to 1 consistently and stop the extra replicas
gracefully. Running jobs retain their leases; reducing capacity does not cancel them.
Allow a cooldown and measure queue wait/CPU/RAM/DB utilization before another change.
Automatic hysteresis/control and complete fleet metrics are not implemented.

Rollback to one worker: stop workers, set both capacity values back to 1, and recreate
the same services with `--scale source-file-worker=1 --scale reconciliation-worker=1
--scale ocr-worker=1 --scale master-sync-worker=1`. Restart API with the same values.
Keep the upgraded queue safety code; no data rollback or migration is needed.

Reproduce PostgreSQL tests with an explicitly isolated `TEST_DATABASE_URL`, plus
`TEST_MONGO_URI` for the native PDF/Mongo test. URLs must satisfy `validate_test_url`:

```bash
ETL_REQUIRE_POSTGRES=1 python -m pytest tests/integration/test_bounded_workers.py tests/integration/test_bounded_pipeline.py tests/integration/test_postgres.py tests/integration/test_reconciliation_audit_queue.py -q
python -m scripts.benchmark_bounded_workers --output BOUNDED_WORKERS_BENCHMARK.json
```

## G. Conclusion

Status: **PARTIAL**. Database admission, per-resource import ordering, per-stream audit
exclusion, expired OCR fencing, transactional processing handoff and scalable settings
are implemented. MasterSync ordering, ownership/auth rules and audit evidence semantics
remain intact. Real PostgreSQL process and MongoDB/native-PDF evidence is available.

Acceptance still requires a green baseline/regression suite, representative end-to-end
mixed-file and metadata benchmarks, scanned OCR/model/search validation and runtime
Docker smoke tests of the rebuilt image. Optional thread-pool hashing and automatic
adaptive concurrency remain deferred. No performance increase or full ACCEPT is claimed.

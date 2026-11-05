# Neo4j: related-learning Active and independent Search rollout

## Current release decision

The local related-learning feature has a separate release boundary:
`NEO4J_RELATED_MODE=active` queries live graph relationships for the resource
detail page. Keep `NEO4J_SEARCH_MODE=shadow` and `NEO4J_FAIL_OPEN=true`; this does
not promote Search ranking, global Vector acceptance or the overall system.
Public deployment defaults for related learning remain `disabled`.

`GRAPH_ACTIVE_GATE` covers official auth, fresh SOURCE/MASTER authority before
and after graph I/O, exact ID/relationship parity, real UI, outage/recovery,
restart and rollback. Unrelated baseline failures remain reported separately.
Missing non-admin DRAFT-owner credentials are `OWNER_LIVE_BLOCKED`; ADMIN-owner
coverage is recorded separately and never presented as non-admin acceptance.
Do not create accounts, change ownership or loosen guards to cover that case.

For backward compatibility, direct callers that omit `neo4j_related_mode`
inherit the previous Search mode behavior. Compose declares the feature mode
explicitly. `NEO4J_RELATED_TIMEOUT_SECONDS=8` bounds the complete related lookup;
the Neo4j query timeout remains independently bounded. A two-slot executor
prevents abandoned timeout work from spawning unlimited tasks.

The endpoint remains `GET /api/v1/resources/{resource_id}/related-learning`.
`ready` with no items is a genuine empty result; `unavailable` is a transient
graph/authority failure. Disabled/Shadow return no suggestions. Official
401/403 remain errors; an inaccessible seed returns a generic 404. No source
title/ID/count is returned for a denied seed. Responses and frontend requests
use no-store; no authorization decision survives the request.

UI shows the authorized title, a relationship reason and the normal detail
link. During graph outage only this section reports unavailability. It never
substitutes fixture or keyword recommendations. The endpoint reads metadata,
SOURCE authority and current curriculum relations; it never loads ContentBags,
OCR text, embeddings or the Search corpus.

Current private local rollout evidence and exact image rollback commands are
in `.local/neo4j/graph-active-final/`. For that deployment, from repository root:

```powershell
# Disable the user-visible feature and restore both pre-rollout images.
.venv\Scripts\python.exe .local/neo4j/graph-active-final/deploy_feature.py --mode disabled --previous-images
# Restore the tested related-learning release; Search stays Shadow.
.venv\Scripts\python.exe .local/neo4j/graph-active-final/deploy_feature.py --mode active
# Recreate the release to verify persisted mode after restart.
.venv\Scripts\python.exe .local/neo4j/graph-active-final/deploy_feature.py --mode active --restart
```

The private helper/receipts are intentionally outside Git and apply only to
this local instance. Portable configuration uses `.env` plus generated
`docker.env`: pin `BACKEND_IMAGE`, `FRONTEND_IMAGE`, `NEO4J_RELATED_MODE` and the
existing graph/auth overlays, then use the normal project launcher. The config
generator preserves explicitly pinned image names. Never reset graph, rerun
OCR or recreate database volumes for a feature rollback.

An approved DRAFT original was retrieved through the official broker and its
bytes matched the SOURCE checksum. Other pending-lifecycle originals remained
policy-blocked. OCR processing approval does not itself grant original-file
read permission. Keep these results separate in release evidence.

MASTER remains migration 0020; no PostgreSQL/Mongo schema change is required.
Graph is a derived read-model, not a source of rights, text or embeddings.

## Search content transport timeout

ContentBag cursor timeouts, including getMore, are converted to the typed
`SEARCH_DATA_TIMEOUT` failure. A partially downloaded corpus is discarded.
Healthy metadata may still be returned with an explicit partial branch status;
if neither branch is ready the response stays503 with a safe message and
Retry-After. No content/permission cache or automatic bulk retry is introduced.

For deployments that preserve exactly five Mongo collections, optional
`SEARCH_METADATA_LIVE_LEXICAL_FALLBACK_ENABLED=true` enables bounded read-only
BoW/Hybrid metadata retrieval when the separate metadata-index schema is absent.
It is entered only after content transport fails, so healthy content ranking
does not acquire extra metadata votes.
It uses fresh metadata projections, existing lexical scoring and final whole
corpus authorization/fingerprint checks. It creates no collection, index,
stored embedding or persisted record. Default isfalse and normal indexed
retrieval is unchanged. Vector is never silently replaced by lexical retrieval.
The UI marks partial results; this mode does not certify full content Search
availability or certify Search Active. The separate related-learning gate does
not depend on global ContentBag transport succeeding.
After an unfiltered content transport timeout, this opt-in mode backs off for
10seconds before another whole-corpus attempt. Skipped requests do not extend
the window. Every request still rebuilds/revalidates metadata; no protected
result or permission decision is cached. Filtered content reads bypass this
availability backoff so a small healthy scope remains usable.

`SEARCH_METADATA_LIVE_DENSE_FALLBACK_ENABLED=true` additionally permits genuine
1024D BGE-M3 title vectors in this failure-only mode. It shares the existing
offline resident sentence-transformer model; FlagEmbedding installation, model
downloads, stored vectors and new collections are not required. Titles and the
original query are encoded without silent truncation. The representation is
explicitly title-only and has its own input/model fingerprint. By default at
most64 authorized metadata resources may be encoded; exceeding that bound
blocks dense fallback rather than pruning the corpus. Each request re-encodes
and revalidates its own metadata; embeddings/permissions are not cached.
`SEARCH_METADATA_LIVE_DENSE_MIN_SIMILARITY` defaults to0.5 for short titles.
The full-content threshold and all healthy ranking paths remain unchanged.

## Light Node contract

The graph is a derived MASTER read-model. The executable allowlist lives in
`src/data_sync_etl/domain/graph_contract.py`: six node labels, seven mandatory
relationship shapes and optional `DERIVED_FROM`. Nodes contain only business
`id`; relationships contain no properties.

| Node | MASTER input |
| --- | --- |
| EducationLevel | Distinct `master_grade_levels.education_level_id`; assert consistent identity |
| GradeLevel | `master_grade_levels.id` |
| Subject | `subjects.id` |
| LearningResource | `master_learning_resources.id` |
| ResourceVersion | `resource_versions.id` |
| ContentUnit | All `content_units.id`, including derived units and history |

| From | Relationship | To | MASTER foreign key/join |
| --- | --- | --- | --- |
| EducationLevel | HAS_GRADE | GradeLevel | grade.education_level_id |
| GradeLevel | HAS_SUBJECT | Subject | subject.grade_id (nullable) |
| LearningResource | HAS_SUBJECT | Subject | resource_subjects |
| LearningResource | FOR_GRADE | GradeLevel | resource_grade_levels |
| LearningResource | HAS_VERSION | ResourceVersion | version.resource_id |
| ResourceVersion | CONTAINS | ContentUnit | unit.resource_version_id |
| ContentUnit | HAS_CHILD | ContentUnit | unit.parent_unit_id (same version) |
| ResourceVersion | DERIVED_FROM | ResourceVersion | version.parent_version_id (optional) |

SOURCE IDs are not substituted for MASTER federation IDs. EducationLevel retains
the identity recorded by the existing flattened MASTER representation; inconsistent
identity or namespace collision blocks projection. No new identity is invented.
ContentBag roots must join to exactly one existing MASTER ContentUnit/version.
No ContentBag, Chunk, User, ACL, text, score or embedding nodes/properties are allowed.

## Host graph operations

Run from repository root with the virtualenv active and existing private
credentials configured. Reuse configured secrets; never replace credentials or
claim a nonempty graph to bypass ownership checks.

| Purpose | Command |
| --- | --- |
| Install optional driver | `python -m pip install -e ".[graph]"` |
| Prepare ignored Docker secrets | `python deploy/scripts/prepare-neo4j.py` |
| Validate loopback/pin/secrets | `python deploy/scripts/check-neo4j-config.py --env-file .env` |
| Start only Neo4j | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-graph.ps1 -Action GraphStart` |
| Stop only Neo4j | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-graph.ps1 -Action GraphStop` |
| Graph container status | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-graph.ps1 -Action GraphStatus` |
| Connectivity | `python -m data_sync_etl.cli graph-preflight` |
| First bootstrap, empty dedicated graph only | `python -m data_sync_etl.cli graph-preflight --claim-empty` |
| Full preview / refresh approved plan | `python -m data_sync_etl.cli graph-sync --full --dry-run` |
| Apply the same validated MASTER snapshot | `python -m data_sync_etl.cli graph-sync --full --apply` |
| Exact parity; nonzero on drift | `python -m data_sync_etl.cli graph-diff` |
| Private counts / property audit | `python -m data_sync_etl.cli graph-status` |
| Independent periodic full/delta refresh | `python -m data_sync_etl.cli run-graph-worker` |
| One scheduled iteration | `python -m data_sync_etl.cli run-graph-worker --once` |

Browser: `http://127.0.0.1:7474`; host Bolt: `bolt://127.0.0.1:7687`.
The graph worker defaults to a 300-second full scan, applying only differences.
Run it in a separate terminal/process; stop that process with Ctrl+C. It is a
fifth, independent worker, never part of the four OCR/metadata workers.
All graph writers must share the same `NEO4J_STATE_DIR` and run on one host in v1;
cross-host concurrent writers are unsupported. Losing the ownership receipt
blocks destructive reconcile; do not forge it or wipe an existing database.

Node/edge diffs are computed automatically on each full scan, including parent
changes and records removed from MASTER. A soft-deleted record retained in MASTER
remains in this structural graph; Search still rejects it using live authority.
There is no event/outbox or resource-scoped incremental subscription in v1.
Do not change SOURCE/Mongo to track graph freshness.

### Ownership and troubleshooting

- GRAPH_OWNERSHIP_UNPROVEN: verify database identity and the original private
  receipt. No automatic adoption of nonempty unowned databases.
- GRAPH_FRESH_DRY_RUN_REQUIRED: rerun dry-run against the current snapshot.
- GRAPH_BAG_MAPPING_DRIFT: inspect source/index mapping; never synthesize units.
- GRAPH_SYNC_BUSY: another writer owns the OS lock; do not remove its lock file.
- GRAPH_UNAVAILABLE: check only graph health/auth/network. Baseline Search and OCR
  remain independent; graph reads never mint rights.
- GRAPH_FAIL_OPEN_REQUIRED: this rollout forbids graph becoming a required Search
  dependency. Keep FAIL_OPEN enabled.

## Supervised workers

The optional `deploy/compose.graph-workers.yml` overlay adds only users-refresh
and graph projection. It never starts the four metadata/file/OCR/reconciliation
workers. Both services use non-root, read-only containers, restart policy,
heartbeat health checks and existing private connections. SOURCE writeback and
storage writes are explicitly disabled. Only successful refreshes advance health.
Three consecutive users-refresh failures exit so the supervisor can recover.
Graph waits are interruptible and graph-unavailable retries are bounded.

Configure `.env`, prepare `docker.env` using the existing private configuration
generator and preserve the existing graph state directory. Do not regenerate
credentials, ownership receipts or graph data to make a check pass.

| Operation | PowerShell command from repository root |
| --- | --- |
| Prepare Docker configuration | `python deploy/scripts/prepare-project-docker.py` |
| Build optional app/worker images | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build -GraphServices` |
| Start app and the two optional services | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -GraphServices` |
| Status | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status -GraphServices` |
| Exact read-only graph comparison from host | `python -m data_sync_etl.cli graph-diff` |
| Independent host graph worker, if Docker graph worker is stopped | `python -m data_sync_etl.cli run-graph-worker` |

Required private/configuration references include `NEO4J_PASSWORD_SECRET_FILE`,
`NEO4J_STATE_HOST_DIR`, existing `CONNECTIONS_FILE`/CA/storage references and
`USERS_REFRESH_SOURCE_ID`. `GRAPH_SERVICES_IMAGE` selects an existing verified
backend image. Both workers must use the same graph-enabled image build.
`NEO4J_SYNC_INTERVAL_SECONDS` defaults to 300. Health maximum ages must cover
normal refresh duration and interval; keep the users heartbeat below auth TTL.

Run exactly one graph writer and one logical users-refresh worker. Stop the old
host graph process and API-container refresh daemon before using the services.
API recreation does not recreate these independent services. A manual Docker
stop/kill suppresses restart policy: use the explicit Start operation to resume.
Process exit through the worker's SIGTERM lifecycle was tested to auto-restart.

The graph state directory contains private ownership proofs, not a PostgreSQL
checkpoint. Windows-host and Docker writers must not run concurrently; the
cross-platform bind mount is not a distributed locking service.

## Verified endpoint handoff

Host and Docker use different Bolt endpoints. A receipt is endpoint-bound, so
copying or inventing its digest would bypass ownership verification. Instead,
the explicit operator tool connects to the already-owned host endpoint and the
API's Docker endpoint, verifies both live database identities, schema and exact
node/edge sets, then records only that verified endpoint. Original proof remains.

```powershell
python scripts/neo4j_authorize_docker_endpoint.py --apply
```

Stop the graph writer during handoff. The tool writes no graph data and refuses
a different database, missing original proof, schema drift or unequal snapshots.
If it refuses, preserve Shadow and investigate; never claim a nonempty graph.

## User-visible feature and rollback

`GET /api/v1/resources/{id}/related-learning` is a separate, optional read path.
It takes at most 100 current authorized resources, traverses only allowlisted
IDs, verifies shared subject/grade relationships against PostgreSQL, rechecks
current SOURCE/MASTER access and returns at most three hydrated titles/links.
No cross-user cache, raw graph identifiers, hashes or diagnostics are exposed.
Deleted, stale, inactive, wrong-version or unauthorized candidates fail closed.

Active may display “Học liệu liên quan” on the existing detail page. It does not
alter Search ranking, scores, snippets, totals or pagination. Shadow/Disabled,
graph outage, timeouts, mismatch and empty sets hide that optional section.
The detail request does not block the main Search response. UI state is scoped
to the verified identity/grants and cleared when they change.

Rollback: set `NEO4J_SEARCH_MODE=shadow` in the effective private Docker env and
restart only API using its existing deployment overlays. Set
`NEO4J_SEARCH_ENABLED=false` to disable all graph Search calls. Preserve graph
volumes and workers. If using `.env` as authority, update it too before the next
configuration generation. No schema rollback, reindex or OCR is needed.

## Private restore and load drills

For an approved offline backup window, stop the graph worker and Neo4j first.
Use the pinned image's `neo4j-admin database dump neo4j --to-path=/backups`
with the existing data volume and a verified private backup directory mounted.
Default volumes are `etl-neo4j_graph-data` and `etl-neo4j_graph-logs`; inspect the
actual project before mounting. GraphStop preserves them. Never use `down -v`.
Changing an initial `NEO4J_AUTH` value does not rotate an existing database's
password. Use the supported rotation procedure; never delete a volume to apply
new credentials. Preparation refuses to overwrite differing existing secrets.

```powershell
python scripts/neo4j_restore_drill.py
python scripts/neo4j_projection_benchmark.py
```

These are explicit local acceptance tools, not startup commands. The benchmark
requires the separately provisioned `etl-neo4j-related` fixture project on port
27687 and its ignored private env. It verifies project/port before synthetic
writes and never writes SOURCE/MASTER/Mongo/live Neo4j. It restores the fixture
graph after a successful run; on failure inspect the isolated project only.

The restore tool reads live Neo4j into a stable logical snapshot, reconstructs
it on a unique isolated donor, stops that donor, uses the pinned Community
`neo4j-admin database dump/load` commands, and checks exact data/schema parity
after restore and restart. It never stops live Neo4j or mounts its live volume.
This is **logical snapshot recovery**, not a physical backup of the live store.
Private dumps, credentials and test volumes remain retained for inspection;
do not commit them or run `down -v` against the live project.

A separate physical drill was completed on 2026-11-05 after explicit operator
approval for a short outage. The graph worker and live Neo4j were stopped, the
live data volume was mounted read-only and copied to a fresh isolated volume,
and live services restarted immediately after copying. Official dump/load from
that physical copy passed exact data/schema parity and isolated restart parity;
the live graph remained unchanged. This is separate from the non-disruptive
tool above. Do not stop live services to repeat it without an approved window.
Evidence: `.local/neo4j/final-active-20261105/physical-restore/latest-report.json`.

Measured local snapshot: 726 nodes / 842 relationships, zero graph diff;
460 current bag roots and 584 content-unit mappings match. These are evidence
counts, never constants. Read-only MASTER/Mongo scan took 2.60 seconds; isolated
10x synthetic graph had 7,260 nodes and 8,420 edges, with exact replay parity.
Twenty graph-query samples at each concurrency 1/5/10 measured p95 about
37/61/35 ms. These are graph-only timings, not end-to-end Search improvements;
warmup/noise explains non-monotonic timings. Python allocation peak is measured,
not process RSS or a full SQL/network round-trip profile.

## Remaining broader-system acceptance (separate from related learning)

- The latest recorded full backend run had 2,878 pass, 14 fail and eight
  optional skips, before subsequent Search/native-Mongo changes. It was not
  repeated for the scoped graph release. Required graph/auth/affected UI gates
  are measured separately; this is not a full-suite PASS.
- Some old tests require enabling disabled 0021, expect historical 0015/384D,
  or expect SOURCE processing without current owner authority. Do not loosen
  guards/change approved 0020/1024D schema to satisfy those expectations.
  Remaining business/architecture/fixture failures still require resolution.
- The operator authorized continued use of the existing private credentials.
  Real MASTER ADMIN/VIEWER login and the deployed detail UI passed. No account,
  password, role or ownership was changed.
- A separate authorized DRAFT-owner profile is absent. ADMIN that happens to
  own a DRAFT does not substitute for the required non-admin owner-positive test.
- The DRAFT broker had unconditional PUBLISHED checks; isolated owner/admin/
  stranger and SOURCE publication-drift regression tests now pass after repair.
  An approved live DRAFT original now passes broker/byte/checksum verification
  for its ADMIN owner; a non-owner VIEWER is denied. Pending-lifecycle originals
  remain blocked by the existing original-file read policy.
- Full authenticated Search latency/control measurements and Search Active canary,
  original-file allowed/denied matrix and release rollback remain unaccepted.

Private current commands, reports and screenshots:
`.local/neo4j/final-active-20261105/`; older evidence:
`.local/neo4j/active-20261105/`.
No blanket `ACTIVE_E2E_PASS` or public rollout is claimed.

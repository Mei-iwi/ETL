# Neo4j Light Node: contract and rollout

## Active acceptance continuation (2026-11-05)

Stage 00 inventory verified feature HEAD `db3c1b8` against fetched main
`d69f73e6`. All thirteen required continuation evidence files are present.
A new read-only graph diff is MATCH with zero writes; projection was not reset
or reapplied. Existing private evidence remains under `.local/neo4j/`.

The rollout stays Shadow until fresh backend/frontend regression, supervised
workers, authorized DRAFT-owner/file checks, isolated related-learning UI,
restore and measured performance gates pass. Historical counts below describe
their recorded snapshots, never required constants. Private credentials must
be current and explicitly authorized; no exposed password may be reused.

Base verified against `origin/main`: `d69f73e6e8df43da2749ec809f8760fd7d9097c4`.
Neo4j is a disposable graph projection of MASTER, never a third SOURCE.
PostgreSQL owns metadata, lifecycle and authority; MongoDB owns content and bags.
Graph failure must not affect OCR, indexing, metadata sync or baseline Search.

## Contract provenance

The requested workbook `Bang_mo_ta_Neo4j_LightNode(4).xlsx` was not available
in the working tree or supplied download directory. The user's supplied prompt
contains the authoritative transcription of its seven sheets. No workbook was edited.

| Workbook sheet | Implementation contract |
| --- | --- |
| Tổng quan | Independent, asynchronous, read-only projection from MASTER |
| Nodes | Six labels below, exactly one property `id` |
| Relationships | Seven mandatory edge shapes below, optional DERIVED_FROM |
| Mapping_ID | MASTER business IDs; bag `_id` is a MASTER ContentUnit root |
| Search_Flow | Authorized candidates only; graph returns IDs, PostgreSQL remains authoritative |
| Cypher_mau | Parameterized IDs; fixed label/type whitelist; no internal Neo4j IDs |
| Quy_tac | No source writes, new relational tables, Mongo collections or ACL expansion |

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

## Safety and rollout decisions

Read MASTER in a repeatable-read, read-only transaction, using ordered keyset pages.
Read Mongo using SELECT-equivalent operations only. Validate complete snapshots
before graph mutation. Reject orphan/cyclic/cross-version parents and duplicate IDs.
Use full scheduled idempotent scans in v1; no new outbox/checkpoint tables.
Compare exact ID/edge sets, not counts alone. Only a verified dedicated graph may
reconcile stale edges/nodes; existing graph ownership must be audited first.

Graph connections are lazy and disabled by default. Search uses bounded batched
lookups after candidate authorization, preserving ranking, snippets, pagination,
current-version rules and final PostgreSQL/SOURCE authority checks. Stale or failed
graph lookups fall back to the existing path. No public graph diagnostic endpoint.

Stages 0–10 are gated and committed separately. Live backfill follows the complete
isolated safety suite. Search remains disabled until isolated tests pass; live
activation additionally requires authenticated canary and outage fallback checks.
Never push automatically. Local evidence belongs under ignored `.local/`.

## Baseline observation (2026-11-04)

Read-only MASTER snapshot: revision 0020, 24 public tables. Expected nodes:
EducationLevel 3, GradeLevel 4, Subject 10, LearningResource 62,
ResourceVersion 63, ContentUnit 584. Expected edges in table order above:
4, 10, 45, 45, 63, 584, 90; optional DERIVED_FROM 1.
These counts describe this snapshot, never constants in projector code.
Only the existing MASTER Docker service was running before this work.

Neo4j configuration references:
[Docker configuration](https://neo4j.com/docs/operations-manual/current/docker/configuration/),
[Docker Compose](https://neo4j.com/docs/operations-manual/current/docker/docker-compose-standalone/),
[Python driver compatibility](https://neo4j.com/docs/python-manual/current/install/).

## Isolated acceptance and existing regression debt

Required Neo4j tests exercise real pinned Community in the isolated test project,
all edge shapes, exact property/schema audits, keyset snapshots, ID federation,
parameter binding, replay, delta, rollback, ownership, missing/stale graph,
Search disabled/shadow/active equality, DRAFT owner/admin/stranger, deletion,
actual graph stop/start, connection reuse and unchanged Search output.
The Search and original-file browser fixture checks passed without skips.

The full unit run produced 2,812 passes, 39 failures and eight optional skips.
All 39 failing node IDs were rerun against a complete archive of the verified
main base: the same 39 failed. They are existing baseline failures, not a green
full regression. Raw reports remain private under `.local/neo4j/`.
Two outdated frontend test doubles were updated to reflect the existing progress
helper/state layout and existing 45-second BoW transport budget; production UI
and Search deadlines were not changed. The broad frontend run also exposed older
browser scenarios for pre-queue reconciliation and pre-SOURCE-viewer behavior;
these are not counted as Neo4j acceptance passes. Required Search/file-viewer UI
checks run independently against the current built application.

Live Search/UI authentication is a separate gate. It was initially AUTH_BLOCKED;
the authorized continuation below records its resolution. Do not bypass official
authentication, create accounts or change roles to pass acceptance.

## Authenticated Shadow continuation (2026-11-04)

This section supersedes the earlier AUTH_BLOCKED notes and deployment status
below. The owner explicitly authorized existing test accounts; their credentials
are private. Official `AUTH_MODE=master_users` login passed for federated ADMIN
and VIEWER. No account, password or role was changed.

- Preserved graph: 726 nodes, 842 relationships; zero missing/extra objects.
  All 460 current ContentBag roots and 584 content-unit mappings match.
- ADMIN and VIEWER: BoW, Vector and Hybrid Search plus context HTTP 200.
  All six responses match graph-disabled baseline on the same application/data.
- Real browser: ADMIN renders all three methods; VIEWER renders BoW and context.
- Private DRAFT: ADMIN search/read allowed; non-owner VIEWER gets zero Search
  hits and metadata 403. This VIEWER owns no DRAFT, so live owner-positive
  coverage is unavailable (isolated coverage passed). The selected original
  file returns 404 even for ADMIN; successful live file retrieval is not claimed.
- Actual graph stop/start: three Search methods fall back with unchanged
  results; recovery reports PARITY/SHADOW. Graph diff stays zero. Diagnostics
  emit static status codes only, never identity, candidates or query content.
- Same 39 failing cases on feature and main `d69f73e6`, with the same interpreter,
  dependencies, reference files, hash seed and test parallelism. Zero new failing
  cases; full regression remains red. 25 focused Shadow/client tests pass.

API now runs the verified dependency image plus the Neo4j/source layer. The
dedicated official `run-users-refresh-worker --source-id postgresql` daemon
restores normal MASTER authentication freshness without bypassing checks. In
this verification it runs inside the API container and must be restarted if
that container is recreated. Normal SOURCE-writing workers remain stopped.

**Keep Shadow and fail-open.** Active and overall REAL_E2E_PASS remain blocked by
the regression gate and live coverage limits above. Search canaries take roughly
13–22 seconds; this work does not claim a speedup or different ranking.
Detailed evidence is in `.local/neo4j/CONTINUATION_REPORT.md`.

## Verified full backfill (2026-11-04)

| Label | Expected | Actual | ID-set diff |
| --- | ---: | ---: | ---: |
| EducationLevel | 3 | 3 | 0 |
| GradeLevel | 4 | 4 | 0 |
| Subject | 10 | 10 | 0 |
| LearningResource | 62 | 62 | 0 |
| ResourceVersion | 63 | 63 | 0 |
| ContentUnit | 584 | 584 | 0 |

| Edge shape | Expected | Actual | Pair-set diff |
| --- | ---: | ---: | ---: |
| EducationLevel / HAS_GRADE / GradeLevel | 4 | 4 | 0 |
| GradeLevel / HAS_SUBJECT / Subject | 10 | 10 | 0 |
| LearningResource / HAS_SUBJECT / Subject | 45 | 45 | 0 |
| LearningResource / FOR_GRADE / GradeLevel | 45 | 45 | 0 |
| LearningResource / HAS_VERSION / ResourceVersion | 63 | 63 | 0 |
| ResourceVersion / CONTAINS / ContentUnit | 584 | 584 | 0 |
| ContentUnit / HAS_CHILD / ContentUnit | 90 | 90 | 0 |
| ResourceVersion / DERIVED_FROM / ResourceVersion | 1 | 1 | 0 |

Total: 726 nodes, 842 edges. Six uniqueness constraints verified. Every node has
only `id`; no edge properties. All 460 current ContentBag roots resolve to the
same version/resource/subject/grade sets as read-only PostgreSQL, in five batches.
All 584 current Mongo content units match MASTER unit/version IDs.
A second apply wrote zero node/edge changes; diff remained zero after GraphStop /
GraphStart. SOURCE/MASTER table names, column signatures and row counts are
identical before/after. MASTER remains 0020. Atlas collection names, validators,
indexes and row counts are identical; there are still exactly five collections.
No seed data, projection checkpoint, source write or new relational schema.

Feature acceptance: 78 required graph tests plus five deployment guard tests
passed without skips; three Search/file browser checks passed. Deployment suite:
99 passed, two environment-specific optional tests skipped (not counted as PASS).
Full backend regression retains the 39 reproduced main failures described above.
This is verified graph backfill, not authenticated live Search acceptance.

## Windows operations

Run from the repository root with the virtualenv active. Set private `.env`
values from `.env.example`. Use a new strong `NEO4J_PASSWORD`; templates contain
none. Keep `NEO4J_SEARCH_ENABLED=false` until authenticated canary approval.
Enable `NEO4J_ENABLED=true` and `NEO4J_SYNC_ENABLED=true` for graph operations.
Do not store Neo4j credentials in the four-key `CONNECTIONS_FILE`.

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

### Optional Docker API profile

Run `python deploy/scripts/prepare-project-docker.py` after configuring `.env` and
preparing Neo4j secrets. Add `-Graph` to the existing PowerShell app launcher
Build/Start commands; it loads `deploy/compose.graph-app.yml` last. It installs the
optional graph driver, mounts a password file and joins the explicit external
`etl-neo4j-graph` network. `NEO4J_RUNTIME=docker` selects
`NEO4J_URI_DOCKER=bolt://neo4j:7687`; never use container loopback for the server.
This overlay starts no database and does not change OCR worker configuration.
It does not implicitly enable Search or populate a graph. The API now runs the verified dependency image plus the graph/source layer
for the authenticated Shadow canaries described above.

V1 active mode consumes verified graph identity resolution, preserving the same
ranking and filters. PostgreSQL validates all relationship IDs; final SOURCE/PG/
Mongo checks remain. It is not a graph ranking or latency optimization. Shadow
only compares mappings. Timeouts, stale/missing mappings and graph outage fall
back, with bounded concurrency and a fraction of the existing Search budget.
No authority/results cache or public graph endpoint is added.

### Backup, rollback and troubleshooting

Data volume: `etl-neo4j_graph-data`; logs volume: `etl-neo4j_graph-logs`. Never use
`down -v`. GraphStop preserves data. Take an offline dump using the pinned image's
`neo4j-admin database dump neo4j --to-path=/backups` with the existing data volume
mounted and a private backup directory mounted at `/backups`; the server must be
stopped and the destination permissions verified. This dump procedure is an
operational instruction, not a claimed completed backup/restore acceptance.
Graph can also be reconstructed from a new validated MASTER snapshot.

Rollback Search by setting `NEO4J_SEARCH_ENABLED=false` and restarting only API;
no source data rollback is necessary. Stop the independent graph worker if
pausing projection. An existing volume ignores initial `NEO4J_AUTH` changes:
use Neo4j's supported password rotation procedure, never delete its volume.
Preparatory scripts refuse to replace differing existing secret files.

- GRAPH_OWNERSHIP_UNPROVEN: verify database identity and the original private
  receipt. No automatic adoption of nonempty unowned databases.
- GRAPH_FRESH_DRY_RUN_REQUIRED: rerun dry-run against the current snapshot.
- GRAPH_BAG_MAPPING_DRIFT: inspect source/index mapping; never synthesize units.
- GRAPH_SYNC_BUSY: another writer owns the OS lock; do not remove its lock file.
- GRAPH_UNAVAILABLE: check only graph health/auth/network. Baseline Search and OCR
  remain independent; graph reads never mint rights.
- GRAPH_FAIL_OPEN_REQUIRED: this rollout forbids graph becoming a required Search
  dependency. Keep FAIL_OPEN enabled.

Local evidence: `.local/neo4j/` and `.local/neo4j/FINAL_REPORT.md`, ignored by Git.
Authenticated Shadow is verified within the stated scope. Complete acceptance
remains REGRESSION_BLOCKED / LIVE_COVERAGE_INCOMPLETE; no overall REAL_E2E_PASS.

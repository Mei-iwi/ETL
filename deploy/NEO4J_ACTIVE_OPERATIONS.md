# Neo4j: Shadow operation and Active acceptance

## Current release decision

Keep `NEO4J_SEARCH_MODE=shadow`, `NEO4J_FAIL_OPEN=true`. Active is not approved.
The related-learning feature is implemented. A candidate API/frontend has been
deployed in Shadow and official MASTER ADMIN/VIEWER canaries have authenticated
using the operator-provided private profiles. Active remains blocked by backend
regression, incomplete current-image Search parity/performance acceptance, and
the missing live non-admin DRAFT-owner case. An ADMIN who owns a DRAFT does not
replace that case. Do not create accounts or change ownership/roles to pass it.

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
availability or justify enabling Neo4j Active.
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

## Remaining acceptance gates

- Fresh full backend run remains red. The full after-fix run had 2,854 pass,
  34 fail, eight documented optional skips; subsequent targeted comparison of
  the original 39 cases has 31 fail/eight pass. This is not a new full-suite PASS.
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
- Full authenticated latency/control measurements and real Active canary,
  original-file allowed/denied matrix and release rollback remain unaccepted.

Private current commands, reports and screenshots:
`.local/neo4j/final-active-20261105/`; older evidence:
`.local/neo4j/active-20261105/`.
No blanket `ACTIVE_E2E_PASS` or public rollout is claimed.

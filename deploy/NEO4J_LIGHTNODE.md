# Neo4j Light Node: contract and rollout

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

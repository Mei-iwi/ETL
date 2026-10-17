# Mongo schema governance

Target contract: 5 collections, 6 business indexes, ContentBag dimension 384.
The runtime contract in src/data_sync_etl/adapters/mongo/schema.py remains unchanged.

## Repository check

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/check_migration_governance.py
```

manifest.json pins normalized validator/index hashes and the schema version. The checker is read-only, blocks .env reads and network connections, and reports drift without changing the manifest or database. Its PASS means repository consistency, not a live schema check.

The Git manifest is external governance state, not an installation receipt. No schema_migrations or sixth collection is created.

## Existing CLI responsibilities

| Mode | Purpose | Writes |
| --- | --- | --- |
| --inventory | Inspect actual collections/indexes | No |
| --check | Compare validators/indexes/documents with contract | No |
| --bootstrap-empty | Bootstrap an empty database | Yes; empty isolated test or separately approved target only |
| --apply | Existing installer/compatibility path | Yes; separate approval and backup required |

Do not use --apply as an automatic version upgrade or run it from API startup.
This phase has no new apply engine and makes no claim that an arbitrary old schema can be safely upgraded.

Before any future live write: confirm exact target/environment, pause relevant writers, backup outside Git, verify restore on an independent target, inspect all documents and schema diff, obtain approval, then use the existing CLI. Run --check afterwards and retain a deployment receipt outside the database with revision/hash/backup reference. Reject incompatible documents; do not delete or fabricate them.

The current user decision forbids Dev writes. No live command or backup was executed in this phase. Historical indexed_chunks, if present, is inventory-only and must not be dropped.

B02 (Excel Fallback enum) remains unresolved documentation; this manifest preserves the approved runtime contract instead of changing it.

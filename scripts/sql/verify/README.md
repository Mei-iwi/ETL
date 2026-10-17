# Read-only verification

catalog.sql works on SOURCE or Master using current_schema(); it reads columns, constraints, indexes, triggers and trigger bodies.
master_preflight.sql is Master-only and returns diagnostic counts for undecided creator/grade/tree changes.

Select a confirmed target and explicit schema first, then run the scripts in a read-only transaction. No connection credentials or target defaults are embedded. The operator must verify current_database()/current_schema() before proceeding.

```sql
BEGIN READ ONLY;
-- SET LOCAL search_path TO the_operator_confirmed_schema;
-- Run catalog.sql, and master_preflight.sql only on Master.
ROLLBACK;
```

Use an explicit real schema in SET LOCAL; do not copy the commented placeholder as approval.
Counts are diagnostics, not permission to insert replacement records, add SOURCE constraints, or delete orphaned data.
No role/grant/DDL is included. The older scripts/sql/source_reader.sql is not a verification script.
These scripts were executed inside read-only transactions on isolated test schemas only.

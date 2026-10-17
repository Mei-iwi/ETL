# Canonical trigger references

- Master: master_current.sql (0006 parent guard, 0007 timestamp body, 6 trigger declarations).
- SOURCE: ../../../alembic_source/sql/triggers/source_current.sql (s0002 functions/triggers, s0003 timestamp body, 26 triggers).

These are **reference-only**, not installers. Historical revision files and original SQL remain byte-for-byte unchanged and retain replay authority. Existing migrations never load these references. Do not execute them manually on an existing database.

scripts/check_migration_governance.py compares the references to AST-extracted historical SQL/function definitions without importing or executing migration modules. Integration tests compare names and function bodies with PostgreSQL installed by the original chains.

Docker already copies alembic/ and alembic_source/, so these references accompany that checkout image. They are not Python runtime resources and are not required by the current wheel. No new revision calls a SQL asset in this phase; wheel runtime loading is therefore NOT_APPLICABLE, not certified PASS.

If a future approved revision needs new executable SQL: keep that revision immutable, ship its versioned asset explicitly (for a wheel, package data + importlib.resources), resolve it independently of CWD, test the installed wheel and Docker, and add preflight/upgrade/reversible downgrade. Do not redirect an old revision to these mutable current references.

# G3 - BLOCKED B04; unchanged import semantics

The Git-content-identical decisions.json restored from 1135ccd records B04 as UNDECIDED and HOLD_CURRENT_BEHAVIOR_UNTIL_USER_DECISION. Windows checkout uses CRLF; normalized content and Git blob hashes match the historical files. This task does not approve new lineage semantics, DDL or historical data repair.

Evidence:
- adapters/source/postgresql.py::version_page does not select parent_version_id.
- application/source_file_import.py selects the latest Master version as parent and refuses a late lower/equal version number.
- source_file_imports has source_version_id -> target_version_id mapping, but its existence does not prove SOURCE tree preservation.

Known defect: SOURCE V1 -> (V2,V3) can become target V1 -> V2 -> V3. Import order 1/3/2 can reject version 2 with BLOCKED_SOURCE_MAPPING. Status: NOT_FIXED.

Two PostgreSQL diagnostic tests reproduce these existing behaviors. Their PASS means reproduction, NOT correct lineage. They are included in G7 acceptance evidence; there is no G3 runtime fix or G3 commit.

Minimal proposal requiring approval: read the true source parent into the existing private DTO; resolve only a completed same-resource source-to-target mapping; keep null parent null; defer/block missing parent with bounded retries rather than use latest; verify branches, delayed parents, duplicate/race behavior and rollback on isolated PG. No new schema is proposed here.

Owner decision needed: behavior when parents arrive late/missing; actor provenance; whether any historical repair is permitted. Existing historical IDs/data must first be assessed read-only. B03/B07/B08/B09 remain unchanged.

# G4 - paged Master catalog reads

Status: PASS for independent optimization; B09 ALL_SOURCE_CATALOG decision remains BLOCKED.

The existing provider/type catalogs remain USED_ONLY_MASTER; inactive/deleted identities are excluded. Canonical identity still uses frequency, newest metadata timestamp and deterministic tie-breaks. A single SQL snapshot selects the page and total; the repository no longer loads every Master resource into Python RAM for these APIs.

Filtering preserves Python strip/casefold semantics, including Vietnamese, sharp-s, dotted I and final sigma. PostgreSQL uses SQL expressions only, SQLite a connection-local pure casefold function; no DDL/function installation. The first nested SQLAlchemy expression hit RecursionError in isolated tests; the flat compiler node fixed it and both PostgreSQL cases were rerun PASS.

Master Data requests page/size/q from the server, 7 rows per page, explicit filter, page reset, cancellation and stale-result keys. Form lookups retain getAll with a finite page-count guard. Routes/response models, layout and form identity values remain unchanged.

Validation:
- Synthetic 1,100-row pagination and legacy canonical/status/Unicode unit coverage included in 982 PASS regression.
- Isolated PostgreSQL catalog tests: 2 PASS, 2.28 s on final code; no unbounded repo.find.
- Frontend 65 PASS, including actual Edge Master Data paging/filter and no-write assertions.
- Build PASS; lint 0 errors/5 existing warnings; Ruff, static governance, diff checks PASS.

B09 alternatives: ALL_SOURCE_CATALOG would expose unused entries and need approved read-only SOURCE mapping/offline behavior; USED_ONLY_MASTER works offline and preserves current IDs but cannot show unused SOURCE catalog entries. No provider/type/role table or projection was added. LOCAL and SOURCE metadata still contribute under the existing contract. Concurrent catalog count/page consistency is obtained by one statement; no live Dev concurrency benchmark was performed.

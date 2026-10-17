# G0 baseline

Branch refactor/etl/api-contract-dataflow-hardening, baseline1ba6b86 (PR60/Prompt05 integrated). One Git repository, clean at start, two old stashes preserved.

48 method/path entries =40 business APIs +8 GET/HEAD methods for4 framework documentation paths.39 documented business operations on38 OpenAPI paths; legacy /admin/sync/master hidden but inventoried. Each route KEEP with frontend/test/script/dynamic template evidence; unknown external usage is not proof of dead code. Shared use cases traced through service_refs, not deleted for lacking frontend calls.

Security matrix is baseline, not claim of authenticated production. Local flags are not identity; schema/data unchanged. B04 lineage and B09 full-source catalog remain pending, as do B03/B07/B08 DDL.

Runtime OpenAPI inspected offline without main app startup. FastAPI uses lazy _IncludedRouter: inventory reads included router registry and compares runtime OpenAPI. No dotenv/network/DB/credential.

Schema checker initially FAIL because two committed governance inputs had been deleted. Operator explicitly approved restoring ONLY contract_snapshot.json and decisions.json from1135ccd. Git blob hashes verified exact. Checker unchanged; no replacement baseline or decision approval. Restored checker PASS drift=[] SOURCE15/Master18/Mongo5,6indexes,384D; live_applied=false. Historical0005 downgrade remains KNOWN_DEFECT.

Baseline run:54unitPASS (inventory,resource_file,resource_lookups,search_access),31.20s. Build/typecheck PASS; lint0errors/5oldwarnings. Ruff audit/test PASS. Focused governance regression rerun before commit. Live Dev/OAuth NOT_RUN, Ground Truth NOT_CONFIRMED. API removals0.

No runtime API changes in G0. G6 asset work is independent and excluded from this commit. No push/merge.

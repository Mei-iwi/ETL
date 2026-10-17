# G5 - bounded file preview

Status: PASS for fixture/isolated contract and real-browser UI; live Dev file acceptance NOT_RUN.

Readiness/access matrix retained:
| State | Result |
| --- | --- |
| Access not allowed / unknown local deployment | 403, no content |
| Resource private/inactive/deleted or missing file/version | 404, no source path |
| Imported but not CONTENT_READY or Index not READY | 409; viewer remains disabled |
| Authorized CONTENT_READY + INDEX READY with valid destination file | 200; Range remains 206 |
| Actual file exceeds format limit | 413 before streaming |
| Unexpected storage service failure | 502 with safe text |

The frontend counts actual stream bytes, not merely Content-Length. Missing, malformed, understated or oversized headers cannot cause unlimited Blob buffering: preview budget is 30 MiB, Office text budget 1 MiB. Cancellation releases the reader; switching resource/version remounts keyed state, aborts old work and revokes object URLs. Existing download/Range and no-store behavior is preserved.

Backend checks actual stat size against the existing format limit and validates the file prefix before FileResponse; no external Office/Drive viewer or direct source URL was introduced.

Validation:
- Frontend stream-budget/cancel/MIME tests 7 PASS; all Frontend tests 65 PASS.
- Backend payload/502 focused tests 2 PASS; related native-format/security batch 12 PASS.
- Existing file Range/permission/readiness regression included in 982 PASS.
- Real Edge desktop/mobile fixture MasterData/File UI PASS; this uses a mock API, not live OAuth/Dev storage.
- Build, Ruff and diff checks PASS; lint unchanged 5 warnings.

Limits: Blob preview is bounded but still holds a supported file in memory. Office download is not in-browser Office rendering. The INDEX READY prerequisite is intentionally not removed.

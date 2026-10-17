# G1 ? verified local administration and sensitive reads

Status: PASS (local-only development contract; no Production authentication claimed).

- Added an early ASGI boundary before upload/body processing; actual launch, environment, loopback peer/Host/Origin and absence of forwarded headers are required.
- Protected legacy aliases, mutations, private resource/processing/index/sync reads; Vite rejects non-loopback proxy peers.
- Chunk and ContentUnit reads use Search access semantics, including inactive/deleted/restricted and REJECTED ancestry. Processing preview does not acquire an Index READY dependency.
- LOCAL ownership is checked before version storage and again under the write lock; SOURCE/UNKNOWN fail closed. Client actor fields cannot establish an identity.
- Published content-addressed bytes are not deleted on transaction failure/no-change; this avoids deleting shared bytes. Unreferenced published bytes may remain; garbage collection is outside scope.
- Successful/error API responses use private, no-store. Upload limits also cover new-version requests.

Validation actually run on the assembled changeset:
- Unit regression: **982 PASS**, 521.24 s, no skips/new XFAIL.
- Focused security/search/viewer regression: **146 PASS**; subsequent actor/metadata/ownership checks: **28 PASS**.
- PostgreSQL/Mongo regression: **57 PASS, 1 historical XFAIL** (migration 0005, NOT_FIXED).
- Frontend: **65 PASS**, real Edge fixture UI included; production build PASS; lint 0 errors, five unchanged warnings.
- Ruff --no-cache, static migration governance and git diff --check PASS.
- No Dev database/credential/OAuth access. SOURCE15 / Master18 / Mongo5 unchanged.

Existing raw TestClient negative tests remain untrusted. The new local client helper attests launch and peer only for tests that explicitly exercise the permitted local deployment; it does not enable admin or change settings.

Limitations: a trusted local process/user is the administrator. This is not authentication/RBAC for a multi-user or public deployment; Production administrative traffic is denied.

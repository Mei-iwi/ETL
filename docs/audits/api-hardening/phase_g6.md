# G6 - existing artwork as favicon

Status: PASS.

Source: frontend/src/assets/img/logoBook.png, actual AVIF RGB 626x626. Source bytes unchanged.
Output: frontend/public/favicon.png, genuine PNG RGBA 64x64, generated deterministically by scripts/build_favicon.py using existing Pillow (no dependency added).

index.html has exactly one image/png icon link. Vite resolves the public-root asset for both root and /etl-test/ deployment. A preliminary BASE_URL placeholder produced a doubled development prefix; the public-root link corrected it and development/build/preview subpath tests all PASS.

Validation:
- Unit PNG signature/size/exact artwork derivation/source hash: 1 PASS.
- Frontend favicon tests: 3 PASS, including actual Vite dev and built preview HTTP 200 + image/png under a subpath.
- Entire Frontend 65 PASS, build PASS; lint 0 errors/5 baseline warnings.
- Visual inspection of PNG confirms the existing book identity. No page/header logo replaced.

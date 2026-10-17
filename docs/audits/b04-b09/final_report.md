# Báo cáo kết thúc B04/B09 — 2026-10-17

> Đây là checkpoint trước yêu cầu push tiếp theo. Trạng thái đồng bộ và xuất bản mới nhất xem [push_report.md](push_report.md).

## Kết luận

Đã triển khai B04/B09 và kiểm thử implementation. Tổng thể PARTIAL: hai cổng commit BLOCKED bởi bộ tài liệu governance gốc thiếu, không phải phê duyệt B04/B09 còn thiếu. Theo đúng prompt, không tạo commit khi còn FAIL/BLOCKED; không tạo commit preflight hoặc commit thứ ba.

- BRANCH: fix/etl/source-lineage-full-catalog.
- BASE_SHA = HEAD: dc3b8f2865c6fe3cb715c95ef49f98a6f32b19ff, main merge PR61.
- Checkpoint ebd1780 tồn tại và là ancestor; không giả định checkpoint là HEAD.
- Worktree riêng: D:/HK7/KLTN/ETL/.worktrees/b04-b09.
- B04: PARTIAL (implementation đã kiểm thử, governance gate BLOCKED), commit NOT_COMMITTED.
- B09: PARTIAL (implementation đã kiểm thử, governance gate BLOCKED), commit NOT_COMMITTED.
- Phê duyệt code mới được lưu ở decisions_overlay.json; không sửa frozen register hoặc giả phê duyệt schema.

## B04

[Chi tiết B04](b04_lineage_acceptance.md). Dùng parent_version_id thật từ SOURCE qua DTO TypedDict; exact lookup theo source version; resolve mapping cùng source_system/resource và target khớp hash/number; root null, nhánh giữ nguyên, version đến muộn không bị tuyến tính hóa. Chặn self/cycle/cross-resource/mapping sai hoặc lỗi và lịch sử sai cây; không sửa target parent cũ.

Dependency được xác minh trước bytes và kiểm tra lại trước đăng ký target dưới UoW/resource lock/lease. PARENT_PENDING dùng retry có sẵn, mặc định 3 lần tự động, backoff base 30s exponential + jitter, cap 3600s; hết hạn RETRY_EXHAUSTED. Manual mặc định tối đa 3 chu kỳ, revalidate mỗi lần. SOURCE ancestry safety bound 128, vượt bound báo terminal error để can thiệp. Parent đến trong budget tự phục hồi; parent đến sau budget cần manual. Diagnostic SQL chỉ SELECT, chưa chạy trên Dev. Historical lineage repair PENDING_DATA_REPAIR.

Files production B04: adapters/source/postgresql.py (private version_page/get_version); ports/source_files.py; application/source_file_import.py. Test: tests/unit/test_source_lineage.py, fixture test_source_file_import.py/test_multiformat.py, tests/integration/test_source_lineage_pg.py, test_lineage_diagnostic.py và source_postgres/test_source_versions.py. Shared test harness: tests/conftest.py thêm app_environment development rõ ràng cho PG test, scripts/run_b04_b09_tests.py chặn dotenv thật. Diagnostic: scripts/sql/verify/b04_lineage_readonly.sql.

## B09

[Chi tiết B09](b09_catalog_acceptance.md). SOURCE catalogs provider/type được đọc trực tiếp, gồm mục active chưa dùng. SQL Master hợp nhất với LOCAL hợp lệ, giữ canonical filter/casefold/count/page và size=7 của UI; không tải master_learning_resources vào Python. Dedup provider bằng id, type bằng code UNIQUE đã có trong SOURCE/API; SOURCE authoritative, không gộp chỉ vì name; identity_overlaps công bố precedence. SOURCE inactive/deleted không được hồi sinh từ LOCAL cùng identity.

SOURCE_OK/SOURCE_EMPTY → ALL_SOURCE_PLUS_LOCAL; SOURCE_DISABLED/SOURCE_UNAVAILABLE → PARTIAL_FALLBACK từ USED_ONLY_MASTER. Credential/permission/schema/programming → SOURCE_ERROR/HTTP503 sanitized, không giả rỗng; reader quá 20.000 catalog rows → SOURCE_LIMIT_EXCEEDED/503, không quảng cáo partial là full. Connect/pool timeout 5s, statement timeout 3s chỉ áp dụng catalog transaction; không auto-retry/cache/sync. Hai database đọc ở hai thời điểm, không có distributed snapshot.

Create/update LOCAL xác thực lựa chọn theo SOURCE authoritative và dùng type ID SOURCE thật. Không sửa metadata resource cũ hoặc quyền SOURCE/UNKNOWN. Grade/subject vốn được sync đầy đủ vào Master, bao gồm mục chưa dùng; test riêng đã chứng minh. Không đổi subject/grade policy.

Files production B09: ports/source_catalogs.py; adapters/source/postgresql.py (catalog_rows + optional catalog error/timeout handling); adapters/persistence/catalog_reader.py; application/list_resource_lookups.py; composition.py; main.py; api/resource_lookup_schemas.py; ports/repositories.py; application/create_resource.py; application/update_resource_metadata.py; adapters/persistence/resource_metadata.py (internal resolved type ID). Frontend: services/libraryLookupApi.ts; components/CatalogNotice.tsx; pages/system/MasterDataPage.tsx; pages/library/LibraryCreatePage.tsx và LibraryUpdateVersion.tsx. Tests: unit/test_source_catalogs.py, integration/test_source_catalogs_pg.py, unit/test_api_hardening_inventory.py (chỉ allowlist đúng additive fields), frontend/tests/sourceCatalog.test.mjs và masterDataViewerUi.test.mjs.

## Kết quả kiểm thử cuối — không cộng batch chồng nhau

| Bộ kiểm tra | Kết quả thực chạy | Thời gian |
|---|---|---|
| Full unit | FAIL: 1.018 PASS, 1 FAIL governance thiếu ERD, 1 SKIP symlink Windows | 368.50s |
| Full isolated integration selection | PASS: 233 PASS, 1 historical XFAIL 0005, 4 deselected, 0 FAIL/SKIP | 305.42s |
| Focused PG cuối B04/B09 | 36 PASS, 0 FAIL/SKIP | 30.17s |
| Focused B04 unit cuối | 19 PASS, 0 FAIL/SKIP | 29.05s |
| Frontend toàn bộ, actual Edge fixture | 70 PASS, 0 FAIL/SKIP | 16.85s |
| Frontend build | PASS (tsc + Vite) | Vite 0.387s, không coi là toàn command |
| Frontend lint | PASS, 0 errors / 5 warning cũ | Không đo riêng |
| Ruff --no-cache | PASS | Không đo riêng |
| git diff --check / cached --check | PASS, index trống | Không đo riêng |
| check_migration_governance.py | FAIL/BLOCKED: Required reference missing ERD_SOURCE_GOC_15_BANG.jpg | Không đo riêng |

Case tự retry với a-child(version=3), z-parent(version=1) được bổ sung sau full suites đã collection; implementation không đổi, case được chạy trong focused unit/PG cuối. Không cộng focused 19/36 vào số 1.018/233. Integration có 4 warning: 3 deprecated embedding method và 1 Pydantic alias warning; không filter để giấu warning.

Lệnh thực tế từ worktree (dùng repository .venv và PYTHONPATH=worktree/src):

```powershell
../../.venv/Scripts/python.exe scripts/run_b04_b09_tests.py -p no:cacheprovider -q -rs --tb=short tests/unit
../../.venv/Scripts/python.exe scripts/run_b04_b09_tests.py -p no:cacheprovider -q -rs --tb=short tests/integration --ignore=tests/integration/test_google_drive_readonly.py --ignore=tests/integration/test_cli_stage_flow.py -k 'not react_ui and not evaluation_ui'
../../.venv/Scripts/python.exe scripts/run_b04_b09_tests.py -p no:cacheprovider -q --tb=short tests/integration/test_source_lineage_pg.py tests/integration/test_source_catalogs_pg.py tests/integration/source_postgres/test_source_versions.py tests/integration/test_lineage_diagnostic.py
../../.venv/Scripts/python.exe -m ruff check --no-cache .
../../.venv/Scripts/python.exe scripts/check_migration_governance.py
node --experimental-strip-types --test frontend/tests/*.test.mjs
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
git diff --check
git diff --cached --name-status
git diff --cached --check
```

Wrapper chỉ tắt repository dotenv và gọi pytest.main; không bỏ/xfail governance. Test PG/Mongo dùng container mới có label etl.b04-b09.test=true, tmpfs, loopback random ports, database etl_test_b04_b09 và schema/database UUID test. Settings _env_file=None; không dùng DB từ .env. Provision model cục bộ revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42, 384D và Tesseract có vie+eng; HF/Transformers offline, không tải hoặc dùng OAuth. Log các batch được giữ trong .test-tmp/b04-b09 (ignored).

Exclusions: test_google_drive_readonly.py có thể dùng credential/live Drive; test_cli_stage_flow.py có subprocess có thể đọc dotenv. Deselected 3 real-model react_ui variants và 1 opt-in evaluation_ui; actual Edge fixture frontend vẫn chạy. Không báo các phần này PASS. Historical 0005 vẫn XFAIL/KNOWN_DEFECT, không sửa revision.

## Schema, API, Git và an toàn

SOURCE 15 / Master 18 / Mongo 5 = UNCHANGED, được xác minh trong DB test cô lập. Tất cả physical implementation SHA256 và Git diff so với frozen baseline đều unchanged; migration heads SOURCE s0003 / Master 0015. Xem schema_preservation.json. Dev/live schema NOT_VERIFIED.

Method/path giữ nguyên. JSON legacy items/pagination/row fields/types giữ nguyên; chỉ thêm optional catalog vào ProviderListResponse, ResourceTypeListResponse, GradeLevelListResponse và CatalogMetadata. b09_openapi_additions.json ghi đúng phần additive; test exact legacy projection PASS. Không thêm header nên không cần CORS exposed headers mới. HTTP không nhận resource_type_id nội bộ.

Bộ 8 tài liệu governance không hiện diện trong checkout. Thư mục người dùng cung cấp D:/HK7/Test/SourceLearningFiles/nguon_hoc_lieu_2026 là học liệu theo môn/lớp, không phải bộ ERD/Excel/SQL audit yêu cầu; tìm đúng tên trong D:/HK7 chưa thấy. Không tạo tài liệu giả hoặc thay baseline để làm PASS. Cần khôi phục đúng bộ TAI_LIEU_DOI_CHIEU có hash gốc rồi chạy lại governance trước hai commit dự kiến.

Không stage file; không commit; không push/merge/rebase/cherry-pick/reset/restore/stash/drop/amend. Worktree feature còn pending code/tests/docs B04/B09 (xem changed_files.json). Worktree main giữ nguyên hai thay đổi giao diện của người dùng; có thêm thư mục worktree mới. Stash list ban đầu/cuối đều trống; không tuyên bố giữ hai stash vốn không có tại checkout này.

Không đọc/sửa .env hoặc secret; không SELECT hoặc ghi Dev; không migration/backfill/reindex/worker trên Dev. Chỉ tạo/dọn hai container test của nhiệm vụ theo ID/name/label đã xác minh, không prune/down -v và không đụng volume Dev. Không sửa nguồn học liệu người dùng.

REMAINING: live OAuth Drive, Browser E2E Dev, Ground Truth xác nhận bởi người dùng, B03/B07/B08, historical 0005, historical lineage repair NOT_RUN/PENDING. Mock Drive và isolated PG/Mongo không tương đương live OAuth/Dev. Những lượt Evaluation fixture thành công không chứng minh chất lượng tìm kiếm. Không tuyên bố Production READY.

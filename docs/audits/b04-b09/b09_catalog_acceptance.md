# B09 — Full SOURCE catalog và offline fallback

## Nghiệm thu bổ sung trên checkout hiện tại — 2026-10-17

Implementation Full SOURCE + LOCAL đã có ở `9695cbf` / PR #64. Giữ nguyên URL, JSON legacy, metadata catalog bổ sung, UI và chính sách SOURCE/LOCAL/UNKNOWN; không triển khai lại. Governance với bộ gốc hiện đã PASS, không sửa frozen contracts hoặc schema 15/18/5.

Regression constructor tái hiện việc B09 đặt pool_timeout=5 lên engine SOURCE dùng chung, khác baseline trước B09. Đã bỏ duy nhất override đó; không tạo engine/pool/cache mới. Network connect_timeout=5 đã có từ trước B09 được giữ nguyên. Thời gian đợi pool giữ cấu hình SQLAlchemy trước đó (mặc định 30 giây), không quảng cáo là deadline 5 giây cho toàn request. Statement timeout 3000ms chỉ đặt transaction-local cho catalog, không tác động lần đọc SOURCE khác.

Đã thêm nghiệm thu PostgreSQL cho timeout không rò sang giao dịch sau và >20.000 catalog rows trả SOURCE_LIMIT_EXCEEDED / HTTP 503 sanitized, không che thành empty hoặc fallback. Giữ active-unused, precedence theo identity, LOCAL hợp lệ, Unicode/count/page, SOURCE_EMPTY, DISABLED/UNAVAILABLE fallback và lỗi permission/schema 503.

Số liệu gate cuối và checksum nằm trong phần nghiệm thu mới của final_report.md. Chỉ tạo một commit sửa lỗi độc lập sau khi PASS. Mô tả pool_timeout=5 và trạng thái thiếu tài liệu ở hồ sơ cũ bên dưới đã được thay thế bởi nghiệm thu này.

**Kết quả cuối lượt này: PASS trong phạm vi cô lập; unit 1053 PASS, integration 238 PASS / 1 historical XFAIL / 4 deselected; focused PG 40 PASS. Live Dev: NOT_RUN.**

### Hồ sơ checkpoint cũ (giữ nguyên để đối chiếu)

Phê duyệt người dùng: 2026-10-17; chỉ code/hợp đồng đọc/kiểm thử, không phê duyệt schema hoặc ghi Dev. Hồ sơ này bổ sung quyết định mới cho B09; decisions.json và contract_snapshot.json lịch sử được giữ nguyên.

## Hiện trạng và ma trận nghiệm thu

Các đường đi đã xác minh trước sửa: ListResourceLookups._page → CatalogQueryMixin.catalog_page; PostgreSQLSourceAdapter.snapshot; Container.resource_lookups; GET /api/v1/providers, /api/v1/resource-types, /api/v1/grade-levels; SQLAlchemySubjectReader.list_subjects/GET subjects; libraryLookupApi.getCatalogPage/getAllCatalogItems; MasterDataPage; LibraryCreatePage và LibraryUpdateVersion.

| Trước | Sau / cách kiểm tra |
|---|---|
| Chỉ provider/type được resource Master tham chiếu | Đọc trực tiếp bảng SOURCE providers/resource_types, kể cả chưa dùng |
| Không có trạng thái completeness | Metadata catalog bổ sung, items/pagination và row fields giữ nguyên |
| SOURCE chưa dùng không có trong form | Form dùng cùng service và giữ metadata trên mảng options |
| Không phân biệt rỗng thật/offline | SOURCE_EMPTY khác SOURCE_UNAVAILABLE/DISABLED |
| Canonical count/filter/page bằng SQL | Giữ SQL casefold/filter/sort/count/page, hợp nhất SOURCE + LOCAL trong SQL |

SOURCE schema đã đối chiếu file alembic_source/schema_v1.sql: providers(id,name,provider_type,is_active,deleted_at); resource_types(id,code,name_vi,is_active,deleted_at); grade_levels(id,education_level_id,code,name_vi,ordinal,is_active,deleted_at); subjects(id,grade_id,subject_name,subject_slug,is_active,deleted_at). Không thêm cột/bảng/index. Grade và subject vốn đồng bộ toàn bộ catalog vào Master, không chỉ các mục được học liệu dùng; kiểm thử thêm grade/subject chưa dùng qua sync và API read model. Giữ nguyên subject/grade association và quyền chỉnh sửa.

## Hợp đồng và identity

SOURCE dùng kết nối riêng qua adapter, transaction REPEATABLE READ READ ONLY và kiểm tra tài khoản không có quyền ghi. Truy vấn catalog chỉ SELECT các cột cần thiết; SELECT set_config chỉ đặt statement_timeout cục bộ transaction. Không JOIN giữa hai database. Snapshot SOURCE và snapshot Master là hai thời điểm độc lập; không tuyên bố distributed snapshot.

Provider identity là id; type identity mà API/form hiện có nhận là code (SOURCE có UNIQUE code). Không tạo ID giả hoặc gộp theo name. SOURCE là authoritative cho cùng identity; metadata LOCAL chỉ từ resource source_origin=LOCAL còn catalog active/chưa soft-delete. SOURCE inactive/deleted không được hồi sinh từ snapshot LOCAL trùng identity. Trùng identity chọn SOURCE và công bố identity_overlaps; UI thông báo precedence. Trùng name khác identity vẫn là hai lựa chọn. SOURCE/UNKNOWN snapshot Master không tự được coi là LOCAL khi SOURCE đang khả dụng.

Response chỉ thêm optional catalog vào envelope ProviderListResponse, ResourceTypeListResponse, GradeLevelListResponse; CatalogMetadata chứa source_status, completeness, identity_overlaps. Không thay method/path hoặc row fields/types. Không cần header mới/CORS expose vì metadata nằm trong JSON. Bằng chứng OpenAPI: b09_openapi_additions.json; test so khớp toàn bộ legacy components sau khi loại đúng các field tùy chọn B09, snapshot G0 không bị ghi đè.

## Tạo/sửa LOCAL từ các lựa chọn SOURCE

CreateResourceUseCase và UpdateResourceMetadataUseCase nhận resolver READ ONLY từ ListResourceLookups. Lựa chọn SOURCE active được kiểm tra theo đúng ID/code và metadata SOURCE; metadata Master cũ cùng identity không chặn một lựa chọn SOURCE hợp lệ. SOURCE resource_type_id thật được dùng cho LOCAL create/update, không sinh ID catalog giả khi type đã tồn tại tại SOURCE. Repository nhận resource_type_id nội bộ từ resolver; HTTP command/schema vẫn không nhận field này từ client. SOURCE unavailable/disabled giữ validation Master cũ, không tự sửa metadata lịch sử.

Các kiểm tra LOCAL-only/UNKNOWN/SOURCE ownership vẫn chạy trước lookup trong update, dưới resource lock. Negative tests cũ giữ nguyên. Test mới dùng lựa chọn SOURCE chưa từng dùng, provider SOURCE đổi tên so với snapshot Master, tạo LOCAL rồi PATCH LOCAL, assert source type ID thật; snapshot resource Master cũ bất biến. Inactive SOURCE và request tự gửi resource_type_id bị từ chối; PATCH SOURCE-owned vẫn 409 và không gọi SOURCE catalog. Tất cả ghi trong test nằm ở DB test cô lập; GET catalogs không ghi SOURCE/Master.

Đường đi bổ sung: application/create_resource.py::_validate_metadata; application/update_resource_metadata.py::execute; adapters/persistence/resource_metadata.py::update_resource_metadata (internal resolved type ID only). Không sửa subject/grade policy, actor, endpoint hay quyền ghi.

## Ma trận offline

| Điều kiện | source_status | completeness / hành vi |
|---|---|---|
| SOURCE SELECT thành công có active | SOURCE_OK | ALL_SOURCE_PLUS_LOCAL |
| SELECT thành công không có active | SOURCE_EMPTY | ALL_SOURCE_PLUS_LOCAL; có thể còn LOCAL |
| Adapter SOURCE không bật/cấu hình không dùng PostgreSQL | SOURCE_DISABLED | PARTIAL_FALLBACK / USED_ONLY_MASTER |
| Network/connection SQLSTATE 08, pool timeout, statement timeout 57014, server shutdown | SOURCE_UNAVAILABLE | PARTIAL_FALLBACK / USED_ONLY_MASTER |
| Sai credential 28P01, permission 42501, SQL/schema/programming 42P01 | SOURCE_ERROR | HTTP 503 sanitized; không ngụy trang empty hoặc fallback |
| Vượt ngân sách 20.000 dòng catalog | SOURCE_LIMIT_EXCEEDED | HTTP 503; không quảng cáo truncated list là đầy đủ |
| Grade | MASTER_MANAGED | Các active grade trong Master |
| Subject | endpoint cũ | Các active subject trong Master, không thay API |

Fallback rỗng vẫn ghi chưa xác minh SOURCE, không khẳng định SOURCE rỗng. Phục hồi đọc trực tiếp ở request kế tiếp, không cache, không auto-sync, không ghi Master. Connect timeout 5s, pool timeout 5s; statement timeout 3000ms trong transaction; không retry tự động. Query count và page hợp nhất trong một câu SQL Master. Frontend giữ page-count guard 1000, cancellation và stale-result key; getAll từ chối trộn các trang khi SOURCE status thay đổi, dedup identity giữa các trang. Các request riêng vẫn eventual consistent, không bảo đảm snapshot qua toàn bộ getAll.

## Fixture và hiệu năng

Unit/PG: 1.100 SOURCE provider chưa dùng + 1 LOCAL → 1.101 lựa chọn; size=7 có 158 trang, trang cuối hai dòng; out-of-range vẫn total=1101, items=[]. Real SOURCE PG: thêm 1.100 provider chưa dùng → filter ĐƠN VỊ trả đúng 1.100, trang 158 còn một dòng. SOURCE type UNUSED xuất hiện dù không có learning_resources reference; inactive bị loại. Test READ ONLY spy xác nhận mọi statement reader bắt đầu SELECT, không đọc learning_resources; số query không tăng theo số entry.

Chỉ chuyển tối đa 20.000 catalog rows chọn cột, không tải resource Master vào Python. SOURCE payload JSON bind được hợp nhất với canonical SQL LOCAL, rồi SQL casefold/count/page; không N+1 theo entry. Chi phí transfer/JSON O(C), SQL canonical Master giữ cơ chế G4. Không tuyên bố benchmark Dev; vượt bound báo lỗi rõ ràng. Unicode tiếng Việt được kiểm thử bằng dữ liệu UTF-8 thật, không chỉ ASCII.

## Kiểm thử và trạng thái

Plan: unit + PG SQL merge, unused/LOCAL/conflict/inactive, fallback/empty/recovery, timeout/auth/permission/programming classification; actual SOURCE SELECT-only; schema 15/18/5; API compatibility; Edge Master paging/filter/status desktop/mobile và create/update options; frontend service cancellation/status-change; full unit/integration, Ruff/build/lint/governance/diff.

Frontend cuối: 70 PASS, 0 skip/fail, 16.85s; actual Edge fixture, không phải Dev E2E. Build PASS. Lint PASS (0 errors, 5 warnings cũ).

Focused unit create/update/catalog/ownership trên code resolver cuối: 70 PASS, 18.89s. Integration rộng trước resolver cuối: 231 PASS, 1 historical XFAIL, 4 deselected, 0 skipped/fail, 312.78s (không dùng làm kết quả cuối khi resolver đã đổi).

Live SOURCE/Dev data: NOT_RUN. OAuth thật, Dev browser E2E, Ground Truth: NOT_RUN. Không sửa .env/secret, không chạy migration/backfill/reindex/worker trên Dev. Static governance đang BLOCKED vì bộ TAI_LIEU_DOI_CHIEU không hiện diện; không tạo baseline giả, không bỏ governance test. Chưa commit khi gate chưa PASS.

Kết quả cuối: PARTIAL / cổng commit BLOCKED do thiếu tài liệu governance. NOT_COMMITTED. Full unit 1.018 PASS / 1 FAIL governance / 1 SKIP symlink (368.50s); integration 233 PASS / 1 historical XFAIL / 4 deselected / 0 FAIL/SKIP (305.42s). Frontend cuối 70 PASS / 0 FAIL/SKIP (16.85s). Build/Ruff/lint/diff PASS; lint còn 5 warning cũ. Không cộng các batch chồng nhau. Focused PG cuối và toàn bộ lệnh/exclusions được ghi trong final_report.md.

Thư mục người dùng cung cấp D:/HK7/Test/SourceLearningFiles/nguon_hoc_lieu_2026 chứa học liệu theo môn/lớp, không chứa 8 tài liệu governance. Đã tìm theo đúng tên tham chiếu trong D:/HK7, chưa tìm thấy; một số thư mục test tạm bị từ chối quyền liệt kê, không nâng quyền để đọc dữ liệu không cần thiết.

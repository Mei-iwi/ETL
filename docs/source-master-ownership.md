# Quyền cập nhật metadata SOURCE → Master

PATCH `/api/v1/resources/{id}/metadata` chỉ sửa học liệu `source_origin=LOCAL`.
`POSTGRESQL` trả 409 `RESOURCE_SOURCE_OWNED_READ_ONLY`; `UNKNOWN`, NULL, giá trị không hỗ trợ
trả 409 `RESOURCE_OWNERSHIP_UNVERIFIED`. Không tự đổi provenance của bản ghi cũ.
Lỗi input nghiệp vụ vẫn 400, DTO không hợp lệ 422, không tìm thấy 404, lỗi lưu trữ 503.

## Đối chiếu cột và đường ghi

| Nhóm cột Master hiện có | Sở hữu / đường ghi |
| --- | --- |
| `title`, `publication_status`, `created_by` | SOURCE cho POSTGRESQL; PATCH LOCAL cho phép `uploaded_by → created_by` |
| `resource_type_id`, `resource_type_code`, `resource_type_name_vi`, `resource_type_is_active`, `resource_type_deleted_at/by`, `resource_type_created_at/updated_at` | Aggregate SOURCE; LOCAL PATCH chỉ nhận code/name_vi theo contract cũ |
| `provider_id/name/type/is_active/deleted_at/deleted_by/created_at/updated_at` | Aggregate SOURCE; LOCAL PATCH chỉ nhận id/name/type theo contract cũ |
| `id`, `created_at`, `updated_at`, `is_active`, `deleted_at`, `deleted_by` | Aggregate SOURCE; LOCAL giữ cơ chế cũ, không mở thêm field PATCH |
| `resource_subjects(resource_id, subject_id, is_primary)`, `resource_grade_levels(resource_id, grade_level_id)` | SOURCE với học liệu POSTGRESQL; LOCAL PATCH theo validation môn/khối hiện có |
| `mongo_id` | Mapping nguồn nếu có; NULL nguồn không xóa mapping sticky. Bootstrap cũ chỉ qua reservation có kiểm tra, khóa dòng, uniqueness |
| `source_origin`, `source_state_hash`, `source_missing_at` | Provenance/guard/reconciliation của ETL; không nhận qua PATCH |
| Các extension MongoDB | Giữ trong MongoDB; không dùng PATCH này để ghi đè aggregate SOURCE |

Use case đọc `SELECT FOR UPDATE`, kiểm tra ownership trước validation quan hệ hay ghi dữ liệu.
Repository dùng cùng domain guard cho cả sửa metadata, thay môn và thay khối để bảo vệ caller không qua API.
Metadata và relations trong cùng transaction; lỗi bất kỳ rollback cả timestamp và quan hệ.
MasterSync vẫn dùng đường ghi aggregate riêng, không gọi các helper chỉnh sửa LOCAL.

GET detail bổ sung `source_origin` (không expose hash). Trang cập nhật chỉ cho sửa LOCAL;
SOURCE/legacy có thông báo chỉ đọc. Backend vẫn chặn nếu client cố gửi PATCH hoặc provenance đổi sau lúc tải form.
Không đổi route, schema DB, OCR/Index/Search/Evaluation hoặc endpoint tạo phiên bản LOCAL.
Form cập nhật kết hợp metadata/tệp hiện có không gửi thao tác khi metadata chỉ đọc; các API phiên bản không bị thay đổi.
Đây là ownership, không thay thế hệ thống xác thực/phân quyền quản trị chưa có trong source hiện tại.

## Hash và tương thích

Giữ nguyên `state_hash()`: loại `source_origin/source_state_hash/source_missing_at`, còn lại hash
trên row qua `_dict`, gồm `mongo_id`, `updated_at` và các trường aggregate hiện có.
Quy tắc `_dict` loại cột nhạy cảm (như password hash) giữ nguyên, không sửa hợp đồng hash ngầm.
Hash hiện không chứa các bảng quan hệ; API/repository guard ngăn đường PATCH quan hệ trái ownership.
Raw SQL có quyền ghi vẫn nằm ngoài API authorization; không tuyên bố hash row phát hiện mọi sửa quan hệ/password.

Lỗi false positive đã xác minh: `reserve_master_mongo_identity()` trước đây đổi `mongo_id`
nhưng không cập nhật `source_state_hash`, nên sync lần sau thấy drift dù thao tác hợp lệ.
Sửa tối thiểu: đọc đủ row có khóa, xác minh hash hiện tại, chỉ sau khi reservation thành công
mới tính hash theo đúng thuật toán cũ trong cùng transaction. Không sửa `updated_at`.
Trường hợp SOURCE cập nhật metadata bình thường vẫn đi qua `sync_upsert` và được tính hash lại.
Reconcile tiếp tục tự cập nhật hash cho tombstone hợp lệ, chỉ áp dụng provenance POSTGRESQL, bỏ qua LOCAL.

Không đổi thuật toán, không migration, không backfill/reset hash trên Dev. Hash cũ còn nhất quán
tiếp tục hợp lệ. Row bị lệch sẵn (kể cả bootstrap cũ) vẫn BLOCKED_LOCAL_SOURCE_CHANGE:
không thể phân biệt an toàn bootstrap lịch sử với raw SQL drift chỉ dựa current row.
Quản trị viên phải đối chiếu SOURCE, mapping và audit trước khi phê duyệt sửa từng record;
không cung cấp lệnh tự "chấp nhận" mọi drift.

## Nghiệm thu

- Trên fixture/test DB: PATCH SOURCE, kể cả giá trị không đổi và xóa quan hệ → 409, row/relations/timestamp không đổi.
- LOCAL: cập nhật hợp lệ → 200; sai môn/khối → 400; lỗi DB sau một phần ghi → 503 và rollback toàn bộ.
- Trong PostgreSQL SOURCE Test, sửa `learning_resources.title/publication_status` rồi chạy Incremental:
  Master nhận đúng dữ liệu, chạy lại không thay đổi, hash vẫn hợp lệ. Cập nhật danh mục/môn/khối tại SOURCE
  theo aggregate hiện có; cập nhật timestamp của resource theo contract nguồn để bảo đảm được quét incremental.
- Sửa Master ngoài sync có chủ ý → BLOCKED_LOCAL_SOURCE_CHANGE; không dùng PATCH hoặc reset hash để né guard.
- Bootstrap Mongo reference hợp lệ → sync lại không false conflict; drift lịch sử vẫn bị chặn.

```powershell
python -m pytest tests/unit/test_resource_metadata_ownership.py tests/unit/test_update_resource_metadata.py tests/unit/test_reconcile_master.py tests/unit/test_master_mongo_references.py -q
node --experimental-strip-types --test frontend/tests/resourceMetadataApi.test.mjs
node --experimental-strip-types --test frontend/tests/resourceMetadataUi.test.mjs
python -m pytest tests/integration/source_postgres/test_resource_ownership.py -q
```

Integration PostgreSQL chỉ chạy với `TEST_DATABASE_URL` và `TEST_SOURCE_DATABASE_URL` đã cấu hình an toàn
theo fixture hiện có. Không dùng URL Dev/Production làm URL test, không tạo DB Test bằng yêu cầu này.

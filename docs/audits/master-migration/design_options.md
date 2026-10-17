# Phương án Master và migration matrix — chưa thay đổi schema

Theo xác nhận người dùng, phê duyệt hiện tại chỉ gồm governance/kiểm thử. Mọi thay đổi thiết kế dưới đây đều BLOCKED riêng chờ quyết định; không cập nhật schema hoặc dữ liệu lịch sử.

Sau nghiệm thu Prompt 02, người dùng yêu cầu giữ nguyên cả lineage (B04) và LOV (B09), không triển khai các phương án bên dưới cho tới khi chốt. B03/B07/B08 tiếp tục chờ phê duyệt schema chính thức. B13 là KNOWN DEFECT, NOT_FIXED; nghiệm thu governance/tests không thay đổi trạng thái lỗi đó.

## Hiện trạng, ảnh hưởng và đề xuất

| Mục | Hiện trạng / ảnh ERD | Ảnh hưởng cần xét | Phương án đề xuất để duyệt | Thay đổi cần phê duyệt |
| --- | --- | --- | --- | --- |
| B03 created_by | Master String(30) nullable, không FK; ảnh nối tới master_users; SOURCE có FK bắt buộc | Orphan cũ, LOCAL/system không có tài khoản, chỉ sync resources khi creator chưa sync; không được tạo user giả | Nếu nghiệp vụ yêu cầu FK: giữ nullable trước, preflight count/orphan, sync users trước resources; named FK Master chỉ sau xử lý nguồn hợp lệ và backup. Không tự đặt NOT NULL | ORM + revision next head, validation/usecase tối thiểu; không sửa SOURCE |
| B07 môn/khối | subjects.grade_id Master nullable, SOURCE NOT NULL; N-N subject/grade độc lập; is_primary chỉ boolean | Học liệu liên khối, LOCAL chưa chọn khối, danh mục chưa đồng bộ; ép một khối có thể loại dữ liệu hợp lệ | Giữ nullable và N-N cho tới khi chốt quy tắc LOCAL. Chốt riêng max-one-primary và consistency, không suy ra UNIQUE từ ảnh | Quy tắc nghiệp vụ rồi mới cân nhắc named check/index/trigger Master; không thêm constraint SOURCE |
| B04 lineage | Source file page không lấy parent_version_id/actors; import nối vào version Master trước; source_file_imports đã có map source→target | Cây nguồn phân nhánh có thể biến thành chuỗi, parent chưa import, actor không có Master user | Nếu cần bảo toàn: đọc parent nguồn qua DTO read-only, tra target bằng source_file_imports, xử lý parent chưa sẵn rõ ràng. Không gán parent suy đoán hoặc remap ID cũ | Chủ yếu adapter/usecase/test cho import mới; có thể không cần cột mới. Không tự backfill version cũ |
| B09 LOV | Providers/types rút từ snapshot resources; grade từ Master grade | Danh mục chưa được dùng không xuất hiện; full Source catalog có thể không sẵn khi nguồn offline | Chốt “danh mục đã dùng” hay “toàn bộ nguồn”. Nếu toàn bộ: ưu tiên read-only adapter/port và endpoint LOV hiện có, không tự thêm bảng danh mục Master | Hợp đồng coverage/fallback và code đọc danh mục; không DDL SOURCE, không API/collection mới theo mặc định |
| B08 grade_code | Master/DTO VARCHAR(50), ảnh Master và SOURCE 30 | Thu hẹp có thể mất giá trị LOCAL >30; mở rộng hiện không làm mất dữ liệu | Giữ hiện tại; đếm length>30 rồi duyệt cập nhật ERD Master hoặc thu hẹp có guard. Không thay SOURCE | Tài liệu hoặc revision Master có preflight nếu thực sự cần |
| B11 OCR | SOURCE một job/version UNIQUE; Master nhiều lịch sử, unique active PENDING/RUNNING | Retry và OCR lịch sử hai tầng không đồng nhất vai trò | Giữ hai workflow độc lập; không nới UNIQUE SOURCE để khớp Master | Không có thay đổi trong phase này |

Preflight đã chuẩn bị ở scripts/sql/verify/master_preflight.sql, chỉ SELECT count. Chưa chạy trên dữ liệu Dev, nên chưa kết luận “không orphan” cho Dev.

## Migration matrix

| Before | After trong phase này | Expected ERD | Reason | Rollback | LIVE_APPLIED? |
| --- | --- | --- | --- | --- | --- |
| SOURCE s0003 / 15 bảng | Không đổi | Giữ thiết kế nguồn gốc | Bất biến | Không cần rollback | No |
| Master 0015 / 10+8 bảng | Không đổi | Giữ gộp danh mục + runtime; đề xuất riêng ở trên | Chưa duyệt DDL | Không có schema delta | No |
| Trigger inline trong 0006/0007, s0002/s0003 | Thêm current reference SQL, không đổi revision | Cùng 6 Master/26 SOURCE trigger | Quản lý và verify, không replay bằng file mới | Bỏ reference sau quyết định; DB không đổi | No |
| 0005 downgrade sai tên index | Giữ lịch sử, tái hiện XFAIL; có runbook phương án | Sau downgrade đúng phải bỏ index đã tạo | Defect đã chứng minh, không rewrite migration đã triển khai | Cleanup/restore index cần quy trình riêng; chưa có writer mới | No |
| Mongo runtime contract / bootstrap CLI | Thêm manifest hash và checker read-only | 5 collections, 6 index, 384D giữ nguyên | Phân biệt bootstrap/check/upgrade | Bỏ manifest/checker sau quyết định, không đổi DB | No |
| B03/B07/B08 schema candidates | BLOCKED riêng, chưa có revision 0016 | Cần chọn theo nghiệp vụ và ảnh Master | Không thay dữ liệu suy đoán | Preflight/backup/rollback plan bắt buộc nếu duyệt | No |
| B04/B09 mapping/coverage candidates | BLOCKED riêng, chưa sửa logic | SOURCE authority, không ép Mongo thành Master | Cần quyết định trước code tương thích | Test và kế hoạch tương thích trước rollout | No |

## Giải thích Master logical và physical

10 logical: master_learning_resources, master_users, master_grade_levels, subjects, resource_subjects, resource_grade_levels, resource_versions, content_units, ocr_jobs, ocr_page_tasks.

8 runtime: sync_checkpoints, sync_runs, sync_auto_config, sync_auto_streams, ocr_page_results, etl_stage_runs, content_unit_texts, source_file_imports. Không bỏ runtime vì ảnh không vẽ.

Provider/resource_type + resource → master_learning_resources; role + user → master_users; education + grade → master_grade_levels.
provider_id/resource_type_id/role_id/education_level_id là tham chiếu logic sau gộp, không thêm FK tới bảng Master không tồn tại. Hai N-N và version/content-unit hierarchy giữ FK vật lý hiện có. mongo_id là mapping xuyên DB, không SQL FK; hashed_password ở PG không được projection sang Mongo.

ContentBag là dữ liệu thứ cấp sau xử lý/chỉ mục, không LOV hoặc metadata nguyên cấp. Không sửa ảnh SOURCE/Excel; tài liệu Master cập nhật ở đây dưới dạng phương án thay vì chỉnh ảnh gốc khi schema chưa đổi.

## Điều kiện trước triển khai live sau này

Xác nhận exact host/database/schema và môi trường; backup ngoài Git + restore verification; preflight dữ liệu thực; test from-zero/from-current và rollback; phân tích lock; duyệt danh sách thao tác; maintenance/writer control; post-check. Không bước nào trong danh sách này là phê duyệt mặc nhiên ghi Dev.

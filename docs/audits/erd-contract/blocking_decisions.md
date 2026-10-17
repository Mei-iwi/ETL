# Quyết định chờ xác nhận — Prompt 01

Không có schema/data thay đổi. B01/B02 chặn khóa tài liệu cuối cùng; các mục khác chặn thay đổi tương ứng, không cản trở việc lưu audit.

Đường dẫn code tương đối src/data_sync_etl/ nếu không nêu khác. CSV có bằng chứng ô Excel và line chi tiết.

| ID | Mức | Bằng chứng / hiện trạng | Quyết định cần xác nhận |
| --- | --- | --- | --- |
| B01 | P1 contract | QUY_TAC_TAO_MA_POSTGRESQL.xlsx, ID Counter!A4 = las_seq; Source Excel/id_counters và alembic_source/schema_v1.sql dùng last_seq | Xác nhận typo tài liệu. Đề xuất sửa Excel sau duyệt, không đổi SOURCE |
| B02 | P1 contract | MO_TA_MONGODB_BOW.xlsx, content_bags!G6 chỉ CHAPTER/LESSON/SECTION; schema.py/domain mongo_documents có 7 Fallback *_TEXT | Xác nhận cập nhật mô tả theo runtime Fallback; không thu hẹp validator hoặc xóa Bag |
| B03 | P1 schema | db/models.py:57 created_by chưa FK; ảnh Master có quan hệ user | Creator bắt buộc hay LOCAL/system nullable? Preflight orphan/thứ tự sync trước FK Master |
| B04 | P1 lineage | adapters/source/postgresql.py:266 không lấy parent/actors; application/source_file_import.py:253 lấy version Master trước làm parent | SOURCE có phân nhánh cần bảo toàn hay ETL tuyến tính? Không đổi SOURCE/ID lịch sử |
| B05 | P1 access | api/routes.py:884, application/orchestrator.py/content_units: chỉ active/deleted, không SearchAccess | API quản trị hay public? Chốt policy/auth trước triển khai ngoài local |
| B06 | P1 access | domain/search_access.py public fail closed; api/search_access.py local_demo; chưa auth Production | Giữ Dev local, không suy PUBLISHED = quyền công khai |
| B07 | P2 relation | db/models.py:134 grade_id nullable; SOURCE NOT NULL; chưa enforce primary/grade consistency | Chốt LOCAL, học liệu nhiều khối/môn trước constraint; SELECT nghi vấn không là phép thêm DDL |
| B08 | P2 type/doc | db/models.py:101/ports.source GradeLevel.code dài 50; SOURCE/ảnh Master dài 30 | Mở rộng hiện không mất dữ liệu; thống nhất DTO/ERD Master sau preflight, không ALTER SOURCE |
| B09 | P2 coverage | application/list_resource_lookups.py:74/:102 lấy provider/type từ resources | LOV toàn SOURCE catalog hay chỉ danh mục đã dùng? Không tự thêm bảng |
| B10 | P2 migration | Chains tĩnh đơn; Mongo SCHEMA_VERSION chưa là revision history; live chưa xác minh | Đích read-only/DB test độc lập phải rõ ràng trước catalog/parity test |
| B11 | P2 workflow | SOURCE OCR version UNIQUE; Master uq_active_ocr_version chỉ PENDING/RUNNING | Giữ hai lịch sử độc lập; không nới UNIQUE SOURCE chỉ vì Master retry |
| B12 | P2 hygiene | pyproject.toml lặp 4 dependency; .venv editable còn trỏ workspace cũ | Bảo trì riêng, không cài/chỉnh ngoài audit |
| B13 | P2 defect | alembic/versions/0005_subject_query_indexes.py:10 tạo ix_subject_grade_active_deleted, :38 downgrade bỏ ix_subjects_grade_active_deleted | Lệch tên index đã chứng minh bằng code; downgrade có thể để lại index thật. Giao phase migration xử lý có kiểm thử, không sửa revision đã áp dụng ở nhánh audit |

## Không coi là defect SOURCE khi chưa có hợp đồng xác nhận

Không tự thêm UNIQUE(version resource/number), UNIQUE(task job/page), UNIQUE(unit version/sequence), anti-cycle SOURCE, trigger grade→subject, primary-subject hoặc grade consistency chỉ vì Master có / báo cáo cũ đề nghị. Giữ 15 bảng, 22 FK. Cần bằng chứng thiết kế được duyệt và preflight trước khi mở lại.

## Đầu vào bước tiếp theo

1. Xác nhận B01 typo và B02 cập nhật mô tả Fallback, không đổi contract runtime.
2. Chốt B03/B04/B07/B09 theo nghiệp vụ trước khi lập migration Master.
3. Cấp đích catalog read-only/DB test riêng nếu muốn nâng NOT_VERIFIED thành VERIFIED. Mọi thao tác ghi cần phê duyệt riêng.

Chưa thực hiện Prompt 02–05, chưa commit/push.

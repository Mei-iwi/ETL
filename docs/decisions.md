# Quyết định thiết kế

1. Dùng Python >=3.12; QA container cố định Python 3.12, Compose PostgreSQL 16.
2. ULID 26 ký tự cho mọi ID kỹ thuật; ID master giữ nguyên stable ID nguồn, tối đa 30 ký tự.
3. `DateTime(timezone=True)` trên PostgreSQL là timestamptz. DTO yêu cầu timezone;
   helper `now()` dùng UTC. SQLite chỉ dùng test logic, adapter chuẩn hóa UTC khi đọc.
4. Master là snapshot denormalized: tên/trạng thái provider, role, education lưu cùng đối tượng phụ thuộc.
5. Bổ sung `master_users.deleted_at/deleted_by`: prompt sync yêu cầu soft delete user dù bảng tối thiểu
   ban đầu chưa có hai cột này. Không hard delete user.
6. `subjects.grade_id` là FK nullable tới master_grade_levels; lịch sử có `ON DELETE RESTRICT`.
   Các trường audit actor là source ID nullable, không thêm FK làm phụ thuộc sync vào thứ tự user.
7. `ocr_page_tasks.claim_token` là token ULID kỹ thuật, khác worker_id: ngăn worker cũ ghi kết quả
   sau thu hồi lease, kể cả worker mới dùng cùng worker_id. Không đưa token vào log.
8. `ocr_page_results` lưu raw/normalized text; không lạm dụng error_message hay image path.
9. `content_unit_texts` là bảng kỹ thuật 1:1 Content Unit, lưu normalized text + FK page_result_id.
   ERD content_units chỉ có metadata; bảng companion tránh bỏ mất nội dung/provenance hoặc thêm vector/chunk.
10. `etl_stage_runs` lưu từng lần postprocess, gồm fingerprint phiên bản thuật toán + kết quả từng trang.
    Không đặt UNIQUE(version, stage): retry cần audit riêng. Khóa version tuần tự hóa output.
11. Postprocess upsert theo (resource_version_id, sequence_no), giữ ID và review khi text/provenance không đổi;
    thay text thì reset review. Toàn bộ transaction rollback nếu parser/DB thất bại.
12. Ingestion sao chép streaming sang `_versions/objects/<sha256>` trước transaction metadata.
    File nguồn mutable không làm hỏng lịch sử. DB rollback có thể để object mồ côi; chưa tự xóa object lịch sử.
13. Tạo OCR job cùng version trả lại job đã tồn tại, kể cả terminal. Baseline không có force-rerun engine;
    retry task và stale recovery là đường phục hồi. Tránh tạo duplicate và ghi đè provenance.
14. Native text gate kiểm tra text không rỗng, >=98% ký tự printable/whitespace, không replacement char.
    Chưa đảm bảo đọc đúng layout/cột; đây là baseline, không phải OCR production.
15. Mọi migration dùng schema snapshot đóng băng `alembic/schema_v1.py`, không import live models.
    Sửa schema tương lai phải tạo revision mới, không sửa revision đã áp dụng.
16. JSON adapter có bộ nhớ bounded: streaming ijson + top-k theo batch, cache lookup 256 entry.
    Đổi lại phải scan nhiều lượt; đây là fixture adapter, không tối ưu cho hàng triệu record.
17. Source aggregate watermark là max(updated_at bản thân, parent, relation).
    Xóa relation phải tăng updated_at resource; không hỗ trợ hard delete/timestamp lùi không có tombstone.
18. Khi process chết giữa một run, audit RUNNING cũ có thể còn lại. Retry tạo audit mới, output/checkpoint vẫn an toàn.
    Chưa có scheduler tự đóng các audit mồ côi; xem timestamp và trạng thái task để debug.
19. Rà soát 02/10/2026: start mặc định không sync; sync_master=True chỉ incremental. Demo seed khi master thiếu.
20. Không ghi mới postprocess_enqueued_at khi chưa có queue; giữ legacy, dùng etl_stage_runs làm audit.
    Không đổi schema hoặc migration 0001–0004. Chưa bổ sung re-OCR, giữ idempotency hiện có.
21. Fixture kiểm tra unique ID/relations toàn stream bằng disk-backed scratch index, tránh mất duplicate
    giữa hai batch. Scratch không chứa password/text và được đóng/xóa sau validation.
22. TEST_DATABASE_URL được ưu tiên; QA default không dùng DB ambient, QA --postgres cấm silent skip.
23. Runtime và QA là hai Docker target; runtime non-root, không có dev dependencies/tests.

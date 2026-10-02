# Hướng dẫn đọc module

1. Bắt đầu README và `cli/main.py` hoặc `api/routes.py`: chỉ parse input và gọi use case.
2. Đọc `composition.py` và factory create_source/create_ocr_engine để thấy cấu hình chọn adapter ở đâu.
3. Đọc `application/sync.py` rồi `ports/source.py`: mapping snapshot, batch/cursor và checkpoint.
4. Theo `application/ingestion.py`: streaming snapshot/hash và khóa version.
5. Theo `application/ocr.py`, `workers/ocr.py`: claim/lease, result, counters, retry/recovery.
6. Kết thúc `application/postprocess.py`: normalization, provenance, atomic upsert và coverage.
7. Sau đó đọc ORM/migration và các test để hiểu đảm bảo ở tầng DB.

## Phân loại bảng

Master: master_learning_resources, master_users, master_grade_levels, subjects;
resource_subjects/resource_grade_levels là relation master.
History/processing: resource_versions, ocr_jobs, ocr_page_tasks, content_units.
Technical: sync_checkpoints, sync_runs, ocr_page_results, etl_stage_runs, content_unit_texts.

## Trace một resource

`fixtures/source/resources.json: resource-1` tham chiếu provider-2/type-2 và subject-1/grade-1.
Sync tạo master snapshot và relation; demo tạo storage/demo/sample.pdf; ingest sao chép vào _versions theo SHA-256,
tạo ULID version. OCR job tham chiếu version; ba task tham chiếu job; result tham chiếu task;
Content Unit tham chiếu version và body tham chiếu result. Page number giữ nguyên từ task đến unit.

## Đảm bảo cần nhớ

- Batch sync và checkpoint commit cùng nhau; FK sai rollback cả aggregate.
- Khóa master resource + unique version_number chống hai ingestion tạo cùng số version.
- SKIP LOCKED chia task cho process; claim_token chặn stale acknowledgement.
- Task result + job counters commit cùng nhau; không tăng counters ở ngoài transaction.
- Khóa version trong postprocess + unique sequence tránh duplicate; lỗi parser giữ output cũ.
- Chưa có exactly-once execution của OCR engine; có thể gọi OCR lại sau crash. DB chỉ chấp nhận một kết quả hợp lệ.

Đổi nguồn/storage/OCR ở `composition.py` bằng adapter triển khai port tương ứng. Không sửa application để import
service cụ thể. Unit test dùng SQLite/Fake; concurrency integration bắt buộc PostgreSQL.
Debug theo stage: sync_runs → resource_versions → ocr_jobs → ocr_page_tasks/results → etl_stage_runs → content_units.

Master Sync và Resource ETL độc lập: chạy sync-master trước khi ingest resource chưa có.
start không full sync mặc định. postprocess_enqueued_at không được ghi mới khi chưa có queue;
đọc etl_stage_runs để biết stage chạy/thành công/thất bại. Docker target runtime chạy app/worker,
target qa chạy verify.py. Đọc scripts/verify.py và tests/conftest.py để hiểu precedence URL test
và kiểm tra current_schema trước migration/cleanup. Hướng thay nguồn thật nằm ở production_source_adapter.md.

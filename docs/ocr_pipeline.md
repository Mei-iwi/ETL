# OCR theo trang

`create_job` khóa resource_version, dùng PDFProcessorPort đếm trang và tạo N task cùng transaction.
Partial unique index bảo vệ một active job/version. API/CLI lặp lại trả cùng job kể cả đã terminal.
PDFProcessor PyMuPDF mở file theo path, không đọc toàn PDF vào byte buffer Python.

Claim dùng SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1, theo created_at/id. Trong transaction,
task thành RUNNING với worker_id, claim_token mới, started_at/heartbeat_at; job thành RUNNING.
Khóa job chỉ trong transaction ngắn. Render/OCR chạy ngoài DB transaction.

Worker gửi heartbeat mỗi timeout/3 giây bằng thread và session riêng. Success/failure/heartbeat kiểm tra
status RUNNING + đúng claim_token dưới row lock. Worker cũ không thể xác nhận sau recovery hoặc claim khác.
Kết quả và SUCCEEDED commit chung với job counter dưới khóa job; nhận kết quả trùng không tăng counter lần nữa.
Result lưu raw_text/confidence/engine/version/metadata và page_task_id UNIQUE.
Image path chứa task ID + claim token, tránh hai attempt ghi đè cùng file.

Native text có thể đi thẳng vào result với engine_name=native_pdf_text. Nếu cần OCR, render PNG có cạnh tối đa
4096 px rồi gọi OCREnginePort. Engine mặc định báo chưa cấu hình nếu trang scan cần OCR; Fake chỉ bật rõ ràng
cho test/demo và gắn metadata demo=true. Không gọi binary hoặc tải model OCR ngoài ý muốn.

Mỗi failure hoặc lease hết hạn tăng retry_count một lần. retry_count < OCR_MAX_RETRIES → PENDING;
ngược lại FAILED + failed_pages tăng đúng một lần. Error chỉ là mã/thông điệp tĩnh an toàn.
`recover-stale-ocr-tasks` khóa từng task stale bằng SKIP LOCKED, xử lý một task mỗi transaction
để tránh giữ nhiều khóa job theo thứ tự khác nhau. Recovery lặp lại không thay terminal task.

Khi counters đạt total_pages, job terminal và completed_at được ghi. Worker không set postprocess_enqueued_at.
Postprocess không set marker này vì chưa có queue; dữ liệu legacy được giữ nguyên, trạng thái thực thi
nằm ở etl_stage_runs. Không có migration trong đợt rà soát này.

## Mode và runtime OCR thật

`composition.create_ocr_engine` chọn native/fake một cách rõ ràng. Fake không là mặc định.
**REAL_OCR_STATUS: BLOCKED_OCR_RUNTIME** — chưa có binary/model/ngôn ngữ triển khai được cung cấp,
không tích hợp hoặc tuyên bố Tesseract/PaddleOCR đã PASS. Tên mode khác bị từ chối lúc cấu hình.

Khi triển khai Tesseract sau này: adapter implements OCREnginePort, gọi binary không qua shell,
đặt timeout rõ ràng, kiểm tra binary/version/language packs trước nhận task và chuyển lỗi thành message tĩnh.
Chỉ lưu text/confidence/engine/version qua OCRResult, không log stdout/stderr chứa văn bản.
Chọn ngôn ngữ bằng config có kiểm tra (ví dụ eng hoặc vie+eng khi runtime thật có cả hai language packs),
thêm optional dependency/runtime OCR target riêng nếu cần. Các test baseline vẫn không cần binary.

Retry cùng run khác với chủ động re-OCR khi đổi engine/policy. Hiện chưa thêm use case re-OCR;
create_job vẫn reuse latest job kể cả terminal. Partial unique index vẫn bảo vệ active job.
Nếu tương lai thêm re-OCR cần command explicit, khóa version, audit engine/policy và provenance từ
ContentUnitText → OCRPageResult → OCRPageTask → OCRJob; không tự reset terminal job.

Tham khảo cơ chế khóa:
[PostgreSQL 16 SELECT locking](https://www.postgresql.org/docs/16/sql-select.html),
[SQLAlchemy Select.with_for_update](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update).

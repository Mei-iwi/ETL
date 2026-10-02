# Status lifecycle

| Đối tượng | Trạng thái baseline | Chuyển trạng thái |
|---|---|---|
| Resource Version | INGESTED | trạng thái review về sau chưa triển khai |
| OCR Job | PENDING, RUNNING, COMPLETED, COMPLETED_WITH_ERRORS, FAILED | pending → running → terminal |
| OCR Page Task | PENDING, RUNNING, SUCCEEDED, FAILED | pending → running → succeeded; lỗi → pending hoặc failed |
| Content Unit review | UNREVIEWED | retrieval_eligible=false khi tạo/thay nội dung |
| Sync Run | RUNNING, COMPLETED, FAILED | một run gồm các transaction batch |
| ETL Stage Run | RUNNING, COMPLETED, FAILED | audit mỗi lần postprocess |

Job COMPLETED khi tất cả trang thành công. COMPLETED_WITH_ERRORS khi có ít nhất một trang hết retry,
kể cả tất cả trang thất bại; coverage phải hiển thị rõ. FAILED được dành cho lỗi toàn job trong mở rộng sau,
không tự dùng khi chỉ một page fail. Không tạo job cho file không đọc được hoặc PDF không có trang.
DB dùng varchar để linh hoạt, Python domain có enum cho version/job/task/review.
Counters bị ràng buộc nonnegative và tổng không vượt total_pages.

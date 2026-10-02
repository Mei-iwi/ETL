# Troubleshooting

| Triệu chứng | Kiểm tra và xử lý |
|---|---|
| DB connection / health 503 | Docker Desktop đang chạy; `docker compose -p etl-master-data ps`; password trong DATABASE_URL khớp POSTGRES_PASSWORD. Connection timeout 5 giây. Nếu localhost bất ổn thử 127.0.0.1; chạy QA qua Docker network để loại trừ firewall host. |
| Sai password sau sửa .env | PostgreSQL volume đã khởi tạo không tự đổi password theo env. Dùng tài khoản quản trị cập nhật password hoặc dùng DB test mới; không xóa volume có dữ liệu để thử. |
| Migration lỗi | `alembic current`, `alembic heads`; dùng đúng DATABASE_URL; migrate trước sync. Không gọi create_all. Chỉ downgrade trên DB/schema test riêng. |
| TEST_DATABASE_URL không như dự kiến | --postgres ưu tiên environment rồi .env TEST_DATABASE_URL, cuối cùng mới DATABASE_URL. URL explicit rỗng/sai bị fail, không fallback. --db-host chỉ thay host đã chọn. |
| QA từ chối DB | Kiểm tra nhãn prod/production, query override host/options và remote guard. Không bỏ guard để chạy lên production; chọn DB test riêng, cấp quyền giới hạn. |
| NO_CHANGE | Cùng bytes với latest version nên không tạo version/job mới. Nếu lần trước dừng giữa chừng, dùng resume-etl/create-ocr-job bằng version ID hiện có. |
| Resource not found khi ingest | Chạy sync-master riêng trước ingest. Orchestrator không tự full sync nữa. |
| Source production chưa cấu hình | SOURCE_ADAPTER=production trả BLOCKED_SOURCE_MAPPING; xem production_source_adapter.md để chuẩn bị mapping vật lý. |
| Checkpoint đứng yên | Kiểm tra sync_runs FAILED, FK nguồn, timestamp/cursor; sửa source và chạy incremental. Field đổi nhưng timestamp giữ nguyên: full sync + sửa nguồn. |
| Duplicate version race | Dùng ResourceIngestion, không INSERT trực tiếp. Khóa resource và unique constraint phải còn. Xem test_concurrent_ingestion. |
| OCR task RUNNING lâu | Heartbeat_at đã quá timeout thì recover-stale-ocr-tasks; sau đó chạy worker. Kết quả worker cũ bị fencing token từ chối. |
| Trang scan lỗi / OCR binary chưa cài | Baseline không dùng Tesseract. Native-only báo lỗi an toàn; thêm adapter thật hoặc chọn OCR_ENGINE=fake chỉ cho demo. Không coi fake text là OCR thật. |
| Trang FAILED | Xem retry_count, status, engine cấu hình; document text không nằm trong error_message. Baseline terminal task không tự reset; sửa nguyên nhân trước khi bổ sung quy trình re-OCR có audit. |
| Postprocess rollback | Job phải terminal; xem etl_stage_runs status. Sửa parser hoặc dữ liệu rồi chạy lại; units cũ còn nguyên. |
| Audit RUNNING cũ sau crash | Đây là lần thực thi bị ngắt, không đồng nghĩa worker vẫn sống. Kiểm tra task heartbeat và tạo run mới bằng lệnh resume. |
| pytest lỗi quyền thư mục Temp | Chạy scripts/verify.py: dùng thư mục tạm riêng trong workspace .test-tmp; không dùng thư mục hệ thống thuộc phiên sandbox khác. |
| PostgreSQL tests bị skipped | Default verify.py chủ động skip. Dùng --postgres để bắt buộc chạy, hoặc export TEST_DATABASE_URL khi gọi pytest trực tiếp. --postgres có skip sẽ FAIL. |

Không in connection URL/password hoặc toàn văn tài liệu vào ticket/log khi debug.

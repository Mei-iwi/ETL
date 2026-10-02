# Baseline audit — 02/10/2026

## BASELINE_STATUS

Rà soát hoàn tất trước khi sửa business code. Baseline chạy được nhưng cần hardening.

## TECH_STACK

Python >=3.12; FastAPI >=0.115,<1; SQLAlchemy >=2.0,<2.1; Alembic >=1.13,<2;
psycopg >=3.2,<4; Pydantic >=2.9,<3; pydantic-settings >=2.5,<3;
PyMuPDF >=1.24,<2; ijson >=3.3,<4; python-ulid >=3,<4. pytest + pytest-asyncio + Ruff.

## ARCHITECTURE

CLI/API → composition → application → ports/domain; adapters triển khai source/storage/OCR/repository.
SQLAlchemy UnitOfWork giữ transaction. Worker có heartbeat thread/session riêng. Không cần rewrite.

## TABLES_AND_CONSTRAINTS

0001: master_learning_resources, master_users, master_grade_levels, subjects,
resource_subjects, resource_grade_levels, resource_versions, content_units, ocr_jobs, ocr_page_tasks.
0002: sync_checkpoints, sync_runs. 0003: ocr_page_results. 0004: etl_stage_runs, content_unit_texts.

Đã đọc models, frozen schema và bốn migration. Chi tiết cột/PK/FK/index ở schema.md.
Các FK đều RESTRICT; self FK version/unit; composite PK hai bảng quan hệ;
unique(resource, version_number), unique(version, sequence_no), unique(job, page_num), unique(page_task_id).
Partial unique active OCR job; index versions(resource,created_at)/hash, tasks(status,created_at)/heartbeat,
stage(version,stage,created_at). Check positive version/page/sequence, nonnegative retries/counters.

Đã xác nhận cursor (effective_updated_at,id), checkpoint cùng transaction batch, streaming SHA-256,
khóa resource khi đánh version, SKIP LOCKED, claim_token fencing, atomic counters và postprocess rollback.

## TESTS_RUN

Baseline Ruff check/format PASS. verify.py: 56 passed, 9 skipped (không chọn PostgreSQL).
verify.py --postgres: 65 passed, không skip, gồm migration và concurrency trên PostgreSQL thật.
Windows Python 3.13; có warning Starlette/httpx và pytest cache permissions ở lượt elevated.

## POSTGRES_TEST_STATUS

PASS — 9 PostgreSQL integration tests chạy trong schema ngẫu nhiên riêng. Không downgrade DB dev.

## P0_ISSUES

Không phát hiện lỗi P0 trong phạm vi local/dev. API hiện không phù hợp public deployment.

## P1_ISSUES

- verify.py ghi đè TEST_DATABASE_URL; thiếu guard DB production và chưa khẳng định current_schema.
- start() full sync mọi master theo mặc định trước ingest, không phù hợp dữ liệu lớn.
- Duplicate source ID chỉ kiểm tra trong batch, có thể bỏ sót khi batch_size=1 hoặc timestamp khác nhau.
- Cần ngăn exception adapter đi qua ASGI rồi bị server log traceback chứa dữ liệu nhạy cảm.

## P2_ISSUES

- Source adapter bị cố định; thiếu cấu hình/fail-fast cho nguồn chưa triển khai.
- Chưa có runtime image, chỉ có QA image.
- postprocess_enqueued_at đang được dùng làm marker thành công dù không có queue.
- Hai tài liệu schema.md/implementation_report.md có ký tự '?' thay dấu tiếng Việt.
- Có .env, caches, test DB, egg-info, storage local; cần gói bàn giao allowlist sạch.
- Scan OCR production và re-OCR chưa triển khai; phải ghi rõ giới hạn.

## DOC_CODE_MISMATCH

Docs chưa phân biệt Master Sync và Resource ETL; marker tên enqueue không thể hiện completion;
README không mô tả precedence TEST_DATABASE_URL hoặc Docker runtime. Docs mô tả latest terminal job
nhưng code chọn latest job rồi yêu cầu nó terminal (không fallback job cũ).

## DO_NOT_CHANGE

Giữ schema Master, hashed_password theo ERD, migration đã áp dụng, khóa/idempotency,
source port, native/fake behavior và dữ liệu local đang được sử dụng. Không thêm queue/vector/search.

## NEXT_PROMPT_READY

YES. Kết thúc pha audit; các sửa đổi tiếp theo thực hiện tuần tự theo yêu cầu hoàn thiện của người dùng.

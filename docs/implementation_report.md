# Báo cáo rà soát và hoàn thiện Data Sync + ETL

Ngày 02/10/2026 (Asia/Bangkok). Phạm vi bộ prompt rà soát 0–10.

## FINAL_STATUS

IN_PROGRESS — đã hoàn tất sửa code và QA Windows, đang nghiệm thu image Python 3.12 và gói bàn giao.
Kết quả baseline trước sửa được ghi riêng tại [baseline_audit.md](baseline_audit.md).

## Các pha đã thực hiện

| Prompt | Kết quả và file chính |
|---|---|
| 0 – Baseline audit | Ruff pass, 56 unit/default pass, 9 PostgreSQL pass; docs/baseline_audit.md |
| 1 – Hygiene/security | ASGI bắt lỗi trước server log, message tĩnh, source/OCR repr che input; scripts/package_source.py dùng allowlist |
| 2 – QA/test DB | TEST_DATABASE_URL ưu tiên; guard production/remote; assert current_schema; --postgres không silent skip |
| 3 – Tách sync/ETL | start mặc định sync_master=False; explicit convenience chỉ incremental; demo bootstrap một lần |
| 4 – Source readiness | SOURCE_ADAPTER=json/production; production fail-fast; kiểm tra duplicate toàn stream bằng scratch index |
| 5 – OCR readiness | native/fake factory rõ; test scan failure, encrypted PDF, raster cap; OCR thật BLOCKED_OCR_RUNTIME |
| 6 – Lifecycle | Reuse terminal job; chưa re-OCR; latest job có tie-break ID; không ghi timestamp enqueue khi chưa có queue |
| 7 – Boundary | Admin off mặc định, local warning, validation chặt, CLI exit/message an toàn |
| 8 – Docker | target runtime non-root không dev deps/tests; target qa giữ full QA; Compose vẫn chỉ PostgreSQL |
| 9 – Docs/encoding | Sửa schema/report UTF-8, tách diagram workflow, bổ sung source mapping/troubleshooting/Git Bash |
| 10 – Regression | Đang chốt kết quả Python 3.12, migration, CLI stage smoke và archive |

## DECISIONS / BACKWARD_COMPATIBILITY

Không thay ERD hoặc migration 0001–0004. SCHEMA_CHANGE: NO. Không có migration mới.
Giữ hashed_password theo ERD; coi là secret at rest, repository public không trả trường này.
Giữ .env, .venv và storage local đang dùng để không phá kết nối/history; gói bàn giao loại toàn bộ các artifact đó.
Không thêm re-OCR khi chưa có nghiệp vụ/policy cụ thể. create_job lặp lại vẫn trả cùng latest job, kể cả terminal.
postprocess_enqueued_at giữ schema/giá trị legacy, không ghi mới; trạng thái thật ở etl_stage_runs.

## FILES_CHANGED

- src/data_sync_etl/config.py, composition.py, main.py.
- src/data_sync_etl/ports/source.py, ports/processing.py.
- src/data_sync_etl/adapters/source/json_fixture.py.
- src/data_sync_etl/application/orchestrator.py, ocr.py, postprocess.py.
- src/data_sync_etl/api/routes.py, cli/main.py.
- scripts/verify.py; mới scripts/package_source.py.
- tests/conftest.py, tests/integration/test_postgres.py; mới test_cli_stage_flow.py.
- Mới tests/unit/test_security_hardening.py, test_qa_harness.py, test_orchestration.py,
  test_source_readiness.py, test_ocr_readiness.py, test_lifecycle.py, test_boundaries.py.
- Dockerfile, .gitignore, .dockerignore, .env.example, README.md và docs liên quan.

## DOCUMENTED_LIMITATIONS

- BLOCKED_SOURCE_MAPPING: chưa có physical DB/API mapping, connection method và watermark/deletion guarantee.
- BLOCKED_OCR_RUNTIME: chưa tích hợp binary/model production; native text và Fake chỉ là các mode hiện có.
- Chưa có force re-OCR, remote admin/authentication, UI review, distributed tracing hoặc tự GC artifacts.
- Không thể nhận diện mọi DB production từ URL: dùng tài khoản test giới hạn quyền và DB test riêng.
- Scratch source validation dùng disk index để bounded RAM, đổi lại scan nhiều lượt; không tối ưu throughput lớn.
- Có deprecation warning Starlette/httpx TestClient; không phải lỗi business/test.

## HOW_TO_RUN

Trong Git Bash, activate `.venv`, sau đó:

```bash
python scripts/verify.py
python scripts/verify.py --postgres
etl sync-master --stream all --full
etl demo-run --fixture-resource-id resource-1
python scripts/package_source.py
```

Chi tiết runtime/QA Docker, Swagger và test DB: [README](../README.md).

## NEXT_SCOPE_NOT_INCLUDED

- Chunk
- Bag of Words
- Embedding/Vector
- Search/Query

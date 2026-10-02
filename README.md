# Data Sync + ETL Master Data

Module độc lập dùng Python >=3.12, PostgreSQL 16, SQLAlchemy 2, Alembic, FastAPI và PyMuPDF.
Hai workflow: **Master Sync** đồng bộ snapshot; **Resource ETL** xử lý file của resource đã có trong master.
Đầu ra kết thúc ở Content Units, không có Chunk/Embedding/Vector/Search.

## Chạy local bằng Git Bash trên Windows

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e '.[dev]'
# Chỉ sao chép khi chưa có .env; giữ nguyên cấu hình local đang dùng.
test -f .env || cp .env.example .env
```

Sửa `POSTGRES_PASSWORD` và `DATABASE_URL` trong `.env` cho khớp. Sau đó:

```bash
docker compose -p etl-master-data up -d --wait postgres
python -m alembic upgrade head
etl sync-master --stream all --full
etl demo-run --fixture-resource-id resource-1
```

PowerShell: gọi `.\.venv\Scripts\python.exe` và `.\.venv\Scripts\etl.exe` thay `python`/`etl`;
không dùng đường dẫn dấu `\` của PowerShell trong Git Bash. Linux/macOS activate `.venv/bin/activate`.

Fixtures ở `fixtures/source/`; `SOURCE_ADAPTER=json` là mode được hỗ trợ.
`SOURCE_ADAPTER=production` dừng với `BLOCKED_SOURCE_MAPPING`, chưa kết nối DB/API nguồn thật.
Demo tạo PDF ba trang native text tại `storage/demo/sample.pdf`, chỉ full sync khi resource demo chưa tồn tại.
Chạy lại cùng file trả `NO_CHANGE`. Nếu sửa fixture, chạy `sync-master` riêng trước demo.

## Chạy từng stage

Đặt PDF có text tại `storage/input/book.pdf`, chạy:

```bash
etl sync-master --stream all --incremental
etl ingest-resource --resource-id resource-1 --bucket input --object-key book.pdf
# Thay VERSION_ID bằng version.id trong kết quả ingest.
etl create-ocr-job --resource-version-id VERSION_ID
etl run-ocr-worker --worker-id worker-1
```

Trong terminal khác, theo dõi và hậu xử lý khi job terminal:

```bash
etl etl-status --resource-version-id VERSION_ID
etl postprocess --resource-version-id VERSION_ID
etl recover-stale-ocr-tasks
etl resume-etl --resource-version-id VERSION_ID
```

Ctrl+C dừng worker; `--once` nhận tối đa một task. Worker ghi kết quả attempt vào DB;
hãy đọc job/task status để phân biệt OCR thành công, retry hoặc terminal failure.
`resume-etl` tạo job nếu thiếu, hoặc postprocess nếu terminal; không tự chạy worker.
`NO_CHANGE` dừng flow ingest; nếu trước đó process bị ngắt thì resume bằng version ID hiện có.
Không có lệnh force re-OCR cùng version trong đợt này; `create-ocr-job` lặp lại trả cùng job.

## OCR mode

- `OCR_ENGINE=native` mặc định: lấy native PDF text. Trang scan retry/fail an toàn khi thiếu OCR engine.
- `OCR_ENGINE=fake`: chỉ demo/test, ghi engine_name=fake; không phải nhận dạng nội dung thật.
- Tesseract/PaddleOCR chưa tích hợp: **BLOCKED_OCR_RUNTIME**. Không tuyên bố hỗ trợ scan production.

## API local và Swagger

```bash
python -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

`GET /health` kiểm tra DB; Swagger ở `http://127.0.0.1:8000/docs`.
Admin mặc định tắt. Đặt `ADMIN_ENABLED=true` trong `.env` rồi restart để thử local.
CLI là công cụ quản trị chính. API admin không có authentication, không publish ra Internet/LAN;
khi bật, startup ghi warning tĩnh. Chưa hỗ trợ remote admin.
API không chạy vòng worker. Body dư field/ID dài hơn 30 ký tự bị từ chối mà không phản chiếu input.

## Kiểm thử và an toàn test DB

`DATABASE_URL` dành cho dev/runtime. **Khuyến nghị `TEST_DATABASE_URL` trỏ DB test riêng**:

```bash
# Tạo DB test một lần nếu chưa tồn tại; không chạy DROP DATABASE.
docker exec etl-master-data-postgres-1 psql -U etl -d etl -c 'CREATE DATABASE etl_test'
```

Điền URL cùng tài khoản phù hợp nhưng database `etl_test` vào `TEST_DATABASE_URL` trong `.env`
hoặc environment. Không đặt mật khẩu trong script/test report.

```bash
python scripts/verify.py
python scripts/verify.py --postgres
```

Default QA chạy Ruff + tests thường; PostgreSQL tests skip có lý do kể cả shell đang có TEST_DATABASE_URL.
Với `--postgres`, thứ tự chọn URL là environment `TEST_DATABASE_URL` → `.env` `TEST_DATABASE_URL`
→ `DATABASE_URL`. URL environment rỗng/sai bị từ chối, không fallback âm thầm.
`--db-host` chỉ thay host của URL đã chọn. PostgreSQL test thiếu cấu hình/skip khi đã yêu cầu sẽ làm QA fail.
Có thể chạy `python -m pytest -q`; khi chạy trực tiếp, test PostgreSQL đọc TEST_DATABASE_URL từ environment.

Guard từ chối DB/host/environment gắn nhãn prod/production và query override host/search_path.
Remote test DB cần `ETL_TEST_DB_ALLOW_REMOTE=true` và tài khoản chỉ có quyền trên DB test.
Không thể suy ra chắc chắn môi trường production từ URL; operator vẫn phải chọn DB đúng.
Test chỉ tạo/drop schema `etl_test_<random>`, kiểm tra current_schema trước migrate/cleanup;
không downgrade DB dev/public. Thư mục test tạm `.test-tmp` không thuộc gói bàn giao.

## Docker runtime và QA

Dockerfile có hai target; Compose dev vẫn chỉ gồm PostgreSQL.

```bash
docker build --target runtime -t etl-master-data-runtime .
docker build --target qa -t etl-master-data-qa .
docker run --rm --network etl-master-data_default --env-file .env etl-master-data-qa python scripts/verify.py --postgres --db-host postgres
```

QA target có tests/dev dependencies. Runtime target chạy non-root, không cài pytest/Ruff, không copy tests.
Trước khi chạy runtime, tạo `.env.runtime` local với DATABASE_URL dùng host `postgres` trong Docker network,
`SOURCE_ADAPTER=json`, `OCR_ENGINE=native`, `ADMIN_ENABLED=false`. File này bị loại khỏi source/image.

```bash
docker run --rm --network etl-master-data_default --env-file .env.runtime etl-master-data-runtime python -m alembic upgrade head
docker run --rm --name etl-api --network etl-master-data_default --env-file .env.runtime -p 127.0.0.1:8000:8000 -v etl_storage:/app/storage etl-master-data-runtime
```

API mặc định listen 0.0.0.0 **bên trong container**, chỉ publish localhost như lệnh trên; admin vẫn tắt.
Healthcheck gọi `/health`. CLI/worker là process/container riêng và phải dùng chung DB + storage:

```bash
docker run --rm --no-healthcheck --network etl-master-data_default --env-file .env.runtime -v etl_storage:/app/storage etl-master-data-runtime etl sync-master --stream all --full
docker run --rm --no-healthcheck --network etl-master-data_default --env-file .env.runtime -v etl_storage:/app/storage etl-master-data-runtime etl run-ocr-worker --worker-id docker-worker
```

Không cài binary/model OCR vào runtime hiện tại. Với bind mount storage, cấp quyền ghi cho UID 10001.

## Gói bàn giao và tài liệu

```bash
python scripts/package_source.py
```

Gói `artifacts/data-sync-etl-source.zip` dùng allowlist, không chứa `.env*` (trừ `.env.example`),
cache, `.venv`, egg-info, test DB, build/dist hay storage. Không gửi nguyên thư mục đang chạy.
Giữ dữ liệu local để không làm mất history; trên bản nhận, tạo `.env` và chạy migrate/demo theo hướng dẫn.

- [Baseline audit](docs/baseline_audit.md), [báo cáo hoàn thiện](docs/implementation_report.md).
- [Kiến trúc](docs/architecture.md), [schema](docs/schema.md), [quyết định](docs/decisions.md).
- [Source contract](docs/source_contract.md), [nguồn production còn thiếu](docs/production_source_adapter.md), [Master Sync](docs/master_sync.md).
- [Versioning](docs/resource_versioning.md), [OCR](docs/ocr_pipeline.md), [Content Units](docs/content_units.md), [lifecycle](docs/status_lifecycle.md).
- [Hai workflow](docs/end_to_end_flow.md), [đọc code](docs/technical_reading_guide.md), [debug](docs/troubleshooting.md), [bảo mật](docs/security.md).

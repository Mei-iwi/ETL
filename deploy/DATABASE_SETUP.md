# Database local: chỉ PostgreSQL MASTER

Mô hình: **SOURCE Neon + MASTER Docker + MongoDB Atlas**. API, giao diện và worker chạy trên host theo [README](../README.md).

## Cấu hình

Chạy PowerShell tại gốc repo, dùng `.env` của dự án:

```dotenv
MASTER_ONLY_PROJECT=etl-master-only
MASTER_ONLY_PORT=5434
POSTGRES_DB=etl
POSTGRES_USER=etl
POSTGRES_PASSWORD=CHANGE_ME
DATABASE_URL=postgresql+psycopg://etl:CHANGE_ME@127.0.0.1:5434/etl
```

Thay mật khẩu trước khi khởi tạo và percent-encode trong URI. `DATABASE_URL` phải khớp thông tin MASTER. SOURCE dùng `SOURCE_DATABASE_URL`; Atlas dùng `MONGO_URI`, `MONGO_DATABASE=etl_content` trong cùng `.env`.

## Khởi tạo MASTER

Image PostgreSQL có sẵn, không cần build Dockerfile:

```powershell
# Chỉ được liệt kê master-postgres.
docker compose --env-file .env -f deploy/compose.master-only.yml config --services
docker compose --env-file .env -f deploy/compose.master-only.yml pull master-postgres
docker compose --env-file .env -f deploy/compose.master-only.yml up -d master-postgres
docker compose --env-file .env -f deploy/compose.master-only.yml ps
docker compose --env-file .env -f deploy/compose.master-only.yml exec master-postgres pg_isready -U etl -d etl
```

Nếu đổi user/database, thay tham số `-U`/`-d` tương ứng. Kết nối host: `127.0.0.1:5434`; trong container là cổng `5432`. Compose chỉ tạo service `master-postgres` và volume dữ liệu, không tạo SOURCE hoặc Mongo local. Không dùng compose ba database cho mô hình này.

Chỉ với **MASTER mới/trống đã xác minh**, tạo schema bằng kết nối MASTER:

```powershell
.venv\Scripts\python.exe -m alembic upgrade 0020
.venv\Scripts\python.exe -m alembic current
```

Giữ revision `0020`; không dùng `upgrade head` hoặc nâng `0021`. Không migration/seed SOURCE. Không tự DROP bảng OCR cũ trên MASTER: ORM hoặc lịch sử tác vụ có thể còn tham chiếu. OCR của luồng mới ghi job/page task/content units vào SOURCE, nội dung vào Atlas, MASTER giữ projection và trạng thái.

## Đồng bộ và Atlas

SOURCE phải sẵn sàng và được cấp quyền. FULL sync với registry ID thực tế, ví dụ:

```powershell
.venv\Scripts\python.exe -m data_sync_etl.cli sync-master --full --stream all --source-id postgresql
```

MASTER mới có thể sinh user ID khác: xác minh mapping SOURCE ADMIN trước khi cập nhật danh tính trong owner contract, giữ nguyên phạm vi quyền đã phê duyệt.

Atlas giữ đúng 5 collection: `users`, `learning_resources`, `ocr_pages`, `mongo_content_units`, `content_bags`; validator `strict/error`, indexes theo contract và embedding 1024D. Kiểm tra bằng:

```powershell
.venv\Scripts\python.exe -m data_sync_etl.adapters.mongo.schema --inventory
.venv\Scripts\python.exe -m data_sync_etl.adapters.mongo.meaning_schema --check
```

Chuẩn bị Atlas mới bằng quyền được cấp; không tự reset dữ liệu hoặc sao chép OCR mất provenance. Khởi động API/worker và bật OCR tự động theo [README](../README.md).

## Dừng và bật lại

```powershell
docker compose --env-file .env -f deploy/compose.master-only.yml stop master-postgres
docker compose --env-file .env -f deploy/compose.master-only.yml up -d master-postgres
```

Named volume giữ dữ liệu qua các lần khởi động. Cập nhật code không cần xóa database. Không dùng `down -v` nếu cần giữ dữ liệu. Đổi `POSTGRES_PASSWORD` trong `.env` không tự đổi mật khẩu database đã khởi tạo.

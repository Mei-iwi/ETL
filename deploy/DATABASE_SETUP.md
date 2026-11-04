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

## Import MASTER sang máy khác

Có hai lựa chọn cho **database đích trống**, chỉ chọn một:

| Mục đích | File | Nội dung |
| --- | --- | --- |
| Tạo cấu trúc, sau đó đồng bộ lại | [master_schema_0020.sql](sql/master_schema_0020.sql) | 24 bảng public, constraints/indexes và revision `0020`; không có dữ liệu nghiệp vụ |
| Khôi phục MASTER hiện tại | `output/master-backup/master_0020_20261103T194639Z.dump` | Backup PostgreSQL custom-format gồm schema và dữ liệu tại thời điểm xuất |

Backup nằm trong `output/` được Git bỏ qua. Chuyển file này riêng, bảo vệ như dữ liệu riêng tư vì có dữ liệu người dùng, mapping và lịch sử xử lý. Mã SHA256 của bản xuất này:

```text
7172db90f6f54d68d0be88b09857ef8148a9f54d9f568fe9cfe950d43444f888
```

Trên máy nhận, chuẩn bị `.env`, khởi động riêng MASTER bằng compose như trên, **chưa chạy migration hoặc ứng dụng/worker**. Các lệnh dưới dùng tên container mặc định, user/database `etl`; thay tương ứng nếu cấu hình khác.

**Lựa chọn 1 — chỉ tạo cấu trúc:**

```powershell
docker cp deploy/sql/master_schema_0020.sql etl-master-only-master-postgres-1:/tmp/master_schema_0020.sql
docker exec etl-master-only-master-postgres-1 psql -U etl -d etl -X -v ON_ERROR_STOP=1 --single-transaction -f /tmp/master_schema_0020.sql
```

**Lựa chọn 2 — khôi phục đầy đủ:**

```powershell
Get-FileHash output/master-backup/master_0020_20261103T194639Z.dump -Algorithm SHA256
docker cp output/master-backup/master_0020_20261103T194639Z.dump etl-master-only-master-postgres-1:/tmp/master_backup.dump
docker exec etl-master-only-master-postgres-1 pg_restore -U etl -d etl --no-owner --no-privileges --exit-on-error --single-transaction /tmp/master_backup.dump
```

Không chạy cả hai lựa chọn vào cùng database; không dùng `--clean` để ghi đè database đang có dữ liệu. Sau import, kiểm tra:

```powershell
.venv\Scripts\python.exe -m alembic current
docker exec etl-master-only-master-postgres-1 psql -U etl -d etl -X -c "SELECT count(*) AS public_tables FROM pg_tables WHERE schemaname = 'public';"
```

Kỳ vọng revision `0020`, 24 bảng public. Schema giữ các bảng OCR legacy vì mã nguồn hiện tại vẫn dùng; không tạo ba bảng component của migration `0021`.

Backup này không bao gồm Neon, Atlas, tệp storage, model, khóa, cấu hình `.env` hay PostgreSQL roles. Cấp role/quyền máy đích theo môi trường được phép và giữ kết nối SOURCE/Atlas phù hợp. Với bản schema trống cần FULL sync; với backup giữ nguyên ID/mapping nhưng vẫn phải kiểm tra owner contract, khóa và kết nối trước khi bật API/worker. Backup là snapshot lúc xuất; cần xuất lại nếu muốn mang theo các thay đổi phát sinh sau đó.

## Dừng và bật lại

```powershell
docker compose --env-file .env -f deploy/compose.master-only.yml stop master-postgres
docker compose --env-file .env -f deploy/compose.master-only.yml up -d master-postgres
```

Named volume giữ dữ liệu qua các lần khởi động. Cập nhật code không cần xóa database. Không dùng `down -v` nếu cần giữ dữ liệu. Đổi `POSTGRES_PASSWORD` trong `.env` không tự đổi mật khẩu database đã khởi tạo.

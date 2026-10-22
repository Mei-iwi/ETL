# Database Docker mới, tách khỏi ứng dụng

Chỉ áp dụng cho **DB mới/trống do bạn quản lý**. DB có dữ liệu: dùng schema/role do chủ DB xác minh, không bootstrap/migration theo hướng dẫn cài mới. Không dùng QA bootstrap cho DB thật.

Chạy từ gốc repo bằng Git Bash. Trước bước 3 cần build Backend image theo [README](../README.md). Giữ tên DB/user mặc định nếu không cần thay đổi.

## 1. Password files và cấu hình

```bash
mkdir -p /d/etl/private
notepad.exe D:/etl/private/master-password
notepad.exe D:/etl/private/source-password
notepad.exe D:/etl/private/mongo-password
test -f /d/etl/private/ops-connections.json || cp deploy/config/ops-connections.example.json /d/etl/private/ops-connections.json
```

Mỗi password file chứa một mật khẩu riêng, không dấu nháy, không `KEY=`, không BOM; lưu đúng tên, không thêm `.txt`. Bảo vệ private bằng ACL; không đưa vào Git/chat/log.

Trong `D:/etl/private/app.env`, cập nhật:

```dotenv
ETL_DATA_NETWORK=etl-data
DB_PROJECT=etl-databases
MASTER_DB_NAME=etl_master
MASTER_DB_ADMIN=etl_master_owner
SOURCE_DB_NAME=etl_source
SOURCE_DB_ADMIN=etl_source_owner
MONGO_DB_ADMIN=etl_mongo_owner
MASTER_PASSWORD_FILE=D:/etl/private/master-password
SOURCE_PASSWORD_FILE=D:/etl/private/source-password
MONGO_PASSWORD_FILE=D:/etl/private/mongo-password
OPS_CONNECTIONS_FILE=D:/etl/private/ops-connections.json
```

Sửa `ops-connections.json`: thay ba `CHANGE_ME` bằng password owner từ các file trên (percent-encode trong URI); giữ host `master-postgres`, `source-postgres`, `mongo`. Đây là JSON owner riêng, không mount cho API/worker.

## 2. Chạy server database

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Network -NetworkName etl-data
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action DbStart -EnvFile D:/etl/private/app.env
docker compose --env-file D:/etl/private/app.env -f deploy/compose.database.yml ps
```

Ba server phải healthy. Compose không publish cổng và không chạy migration/seed. Quản trị bằng `docker compose exec`, app kết nối qua network `etl-data`.

## 3. Schema — owner chạy một lần trên DB mới/trống

Sau khi xác nhận đúng DB, đặt cờ phê duyệt cho từng lệnh (không lưu vào app.env):

```bash
ETL_MAINTENANCE_APPROVAL=APPROVED_DATABASE_MAINTENANCE \
docker compose --env-file D:/etl/private/app.env -f deploy/compose.ops.yml \
  --profile ops run --rm ops master-migrate
ETL_MAINTENANCE_APPROVAL=APPROVED_DATABASE_MAINTENANCE \
docker compose --env-file D:/etl/private/app.env -f deploy/compose.ops.yml \
  --profile ops run --rm ops source-migrate
ETL_MAINTENANCE_APPROVAL=APPROVED_DATABASE_MAINTENANCE \
docker compose --env-file D:/etl/private/app.env -f deploy/compose.ops.yml \
  --profile ops run --rm ops mongo-bootstrap-empty
```

Kỳ vọng 18 bảng Master/15 bảng SOURCE, không tính Alembic, 5 collection Mongo/schema/index 384D. `mongo-bootstrap-empty` từ chối DB không trống. Startup app không chạy các bước này.

## 4. Runtime roles riêng

**Master:** mở psql:

```bash
docker compose --env-file D:/etl/private/app.env -f deploy/compose.database.yml \
  exec master-postgres psql -U etl_master_owner -d etl_master
```

Nhập SQL dưới đây, đặt password tương tác khi được hỏi. Role đã tồn tại thì dừng và đối chiếu quyền:

```sql
CREATE ROLE etl_master_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\password etl_master_runtime
GRANT CONNECT ON DATABASE etl_master TO etl_master_runtime;
GRANT USAGE ON SCHEMA public TO etl_master_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO etl_master_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO etl_master_runtime;
\q
```

**SOURCE:** cấp SELECT rồi đặt password:

```bash
docker compose --env-file D:/etl/private/app.env -f deploy/compose.database.yml \
  exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_owner -d etl_source \
  < scripts/sql/source_reader.sql
docker compose --env-file D:/etl/private/app.env -f deploy/compose.database.yml \
  exec source-postgres psql -U etl_source_owner -d etl_source
```

Trong psql: `\password etl_source_reader`, nhập password riêng, rồi `\q`. Không cấp ghi/superuser/role membership cho reader.

**Mongo:** đăng nhập owner, nhập password khi được hỏi:

```bash
docker compose --env-file D:/etl/private/app.env -f deploy/compose.database.yml \
  exec mongo mongosh --username etl_mongo_owner --authenticationDatabase admin --password
```

Trong mongosh:

```javascript
use etl_content
db.createUser({user: "etl_runtime", pwd: passwordPrompt(), roles: [{role: "readWrite", db: "etl_content"}]})
exit
```

## 5. Runtime connections và khởi động app

Sửa `D:/etl/private/connections.json`, thay password runtime vừa đặt, percent-encode khi cần:

```json
{
  "DATABASE_URL": "postgresql+psycopg://etl_master_runtime:YOUR_MASTER_RUNTIME_PASSWORD@master-postgres:5432/etl_master",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:YOUR_SOURCE_READER_PASSWORD@source-postgres:5432/etl_source",
  "MONGO_URI": "mongodb://etl_runtime:YOUR_MONGO_RUNTIME_PASSWORD@mongo:27017/etl_content?authSource=etl_content",
  "MONGO_DATABASE": "etl_content"
}
```

Quay lại Check/Start trong [README](../README.md). Preflight chỉ đọc, không tự sửa schema/role. Dừng DB bằng `-Action DbStop`, giữ volumes. Đổi password file không đổi password DB đã khởi tạo.

# Xác thực bằng PostgreSQL Auth DB

Đây là **tùy chọn chuyển đổi**, chưa tự thay `single_admin`. Không chạy các lệnh provision/migration dưới đây trên DB Dev/Production khi chưa có chủ DB phê duyệt. Tạo DB mới trên PostgreSQL hiện có, không tạo server mới nếu đã có server phù hợp.

## 1. Chủ PostgreSQL provision DB và hai role riêng

Trong psql bằng DBA, giữ tên mẫu nếu không cần đổi:

```sql
CREATE ROLE etl_auth_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password etl_auth_owner
CREATE DATABASE etl_auth OWNER etl_auth_owner;
CREATE ROLE etl_auth_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password etl_auth_runtime
```

Không gán membership hoặc quyền truy cập bảng SOURCE/Master cho hai role này. Không dùng postgres/superuser làm Auth runtime. Tên DB phải chứa `auth`; schema trống, không trộn bảng nghiệp vụ.

Tạo hai file URL riêng ngoài repo, một dòng UTF-8 không BOM:

| File host | Nội dung mẫu — thay password, percent-encode ký tự đặc biệt |
|---|---|
| `D:/etl/private/auth_owner_url` | `postgresql+psycopg://etl_auth_owner:YOUR_OWNER_PASSWORD@master-postgres:5432/etl_auth` |
| `D:/etl/private/auth_database_url` | `postgresql+psycopg://etl_auth_runtime:YOUR_RUNTIME_PASSWORD@master-postgres:5432/etl_auth` |

Đây là hostname từ network Docker; chạy CLI native thì dùng host/port mà máy local truy cập được. Không truyền URL/password qua argv/Compose plaintext. Trên Linux, runtime URL cần UID10001 đọc được, mode0600; giữ owner URL chỉ cho operator/CLI.

## 2. Migration Auth version1 và bootstrap — chỉ operator chạy

Build code mới trước bằng launcher profile legacy (chỉ dùng để build), xem [README](../README.md). Ví dụ Git Bash, runtime image `etl-portable-backend:local`, network `etl-data`:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network etl-data \
  --mount type=bind,source=D:/etl/private/auth_owner_url,target=/run/secrets/auth_owner_url,readonly \
  --entrypoint python etl-portable-backend:local \
  -m data_sync_etl.security.auth_db_cli migrate \
  --url-file /run/secrets/auth_owner_url --confirm-auth-maintenance
```

CLI tạo **chỉ 4 bảng Auth**, seed admin/operator/viewer, ghi version1; lặp không ghi đè dữ liệu. DDL để review: [0001_initial.sql](../auth_migrations/0001_initial.sql). Startup không migrate.

Sau migration, DBA kết nối **etl_auth**, cấp runtime đúng các quyền:

```sql
GRANT CONNECT ON DATABASE etl_auth TO etl_auth_runtime;
GRANT USAGE ON SCHEMA public TO etl_auth_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON auth_users, auth_user_roles TO etl_auth_runtime;
GRANT SELECT ON auth_roles, auth_schema_versions TO etl_auth_runtime;
```

Bootstrap admin bằng password nhập tương tác:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -it --network etl-data \
  --mount type=bind,source=D:/etl/private/auth_owner_url,target=/run/secrets/auth_owner_url,readonly \
  --entrypoint python etl-portable-backend:local \
  -m data_sync_etl.security.auth_db_cli create-admin --email YOUR_EMAIL \
  --url-file /run/secrets/auth_owner_url --confirm-auth-maintenance
```

Mật khẩu ít nhất 15 ký tự, nhập hai lần, không hiển thị. Hoặc mount file hash cũ read-only rồi thêm `--hash-file /run/secrets/old_admin_hash`; CLI chỉ chấp nhận Argon2id với tham số an toàn. Email/hash ENV chỉ phục vụ bootstrap một lần, không xác thực thường xuyên sau đó. Không import tài khoản SOURCE dù trùng email.

CLI khác: `check-schema`, `list-admin-users`, `reset-admin-password --email EMAIL`, `disable-admin --email EMAIL`; mọi thao tác ghi cần `--confirm-auth-maintenance`. Reset bắt đổi password ở lần login sau và thu hồi phiên cũ. Khôi phục account bị khóa/inactive có thể dùng reset kèm `--unlock` theo quy trình operator; không có API công khai bỏ quyền hoặc password demo.

## 3. Cấu hình API

Local `.env`:

```dotenv
AUTH_MODE=db_users
AUTH_DATABASE_URL_FILE=D:/etl/private/auth_database_url
AUTH_ORIGIN=http://127.0.0.1:3000
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_DEV_ADMIN_BYPASS=false
ADMIN_ENABLED=true
```

Giữ signing/proxy key và session path hiện có. Với native, URL file dùng địa chỉ PostgreSQL host thực (không mặc định hostname Docker). Không cần ADMIN_LOGIN_EMAIL hoặc ADMIN_PASSWORD_HASH_FILE để login DB mode.

Docker `D:/etl/private/app.env`:

```dotenv
AUTH_MODE=db_users
AUTH_DATABASE_URL_SECRET_FILE=D:/etl/private/auth_database_url
AUTH_ORIGIN=http://127.0.0.1:8080
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
ADMIN_ENABLED=true
# Giữ SESSION_SIGNING_KEY_SECRET_FILE và AUTH_PROXY_KEY_SECRET_FILE đang có.
```

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Check -AuthProfile db-auth -EnvFile D:/etl/private/app.env -Drive
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Start -AuthProfile db-auth -EnvFile D:/etl/private/app.env -Workers -Drive
```

Bỏ `-Drive` nếu không dùng Drive. Bash/Linux: `ETL_AUTH_PROFILE=db-auth`; TLS: profile `db-auth-tls`, Origin HTTPS và Auth DB `sslmode=verify-full`/CA hợp lệ. Auth URL secret chỉ mount API, không frontend/worker; không thêm vào `connections.json` nghiệp vụ. Một API replica; SQLite session volume cần được giữ.

## 4. Quản lý và rollback

Sau login, Admin mở **Hệ thống → Tài khoản** để tạo/khóa/mở khóa/active/roles/reset. Operator cần permission sync/process/index/evaluation và resource grants cho thao tác theo phiên bản; Viewer chỉ đọc học liệu được cấp và đủ điều kiện reviewed/eligible. Tài khoản tạo/reset phải đổi password qua `/account/password` trước khi dùng hệ thống. Password tạm phải chuyển qua kênh riêng được phép.

Chuyển mode làm các phiên cũ cần login lại. Restart cùng mode/key/SQLite volume giữ phiên; đổi password/role/status/grants thu hồi các phiên của user đó. Không cho khóa/vô hiệu/hạ quyền Admin hoạt động cuối, kể cả concurrent requests.

Rollback có kiểm soát: chọn `AUTH_MODE=single_admin`, profile `auth`, giữ hash/email/key cũ; không bật bypass. Không xóa Auth DB/tài khoản hoặc làm downgrade schema. Chuyển máy cần backup/restore Auth DB **và** SQLite sessions/signing/proxy keys, không chỉ image tar. Chưa chứng nhận nhiều API replicas, TLS thật hoặc máy thứ hai.

## 5. Kiểm thử repository trong Docker (tùy chọn cho người phát triển)

Image app/worker không cần Git hoặc tài liệu kiểm toán. Các test inventory/governance/favicon cần thêm Git, assets, frozen audit JSON và tám file trong `TAI_LIEU_DOI_CHIEU/`. Bundle đối chiếu không có sau pull phải lấy từ người quản lý dự án; không tạo dữ liệu giả hay bỏ assertion để làm test đạt.

```bash
docker build -f deploy/docker/backend.Dockerfile --target qa -t etl-portable-backend:qa .
docker build -f deploy/docker/qa-repository.Dockerfile \
  --build-arg QA_IMAGE=etl-portable-backend:qa -t etl-db-auth-qa:repository .
# Windows/Git Bash, Python venv đã cài project theo README:
./.venv/Scripts/python.exe deploy/tests/run_repository_qa.py
# Linux: .venv/bin/python deploy/tests/run_repository_qa.py
```

Runner dùng mạng `none`, root filesystem read-only, UID10001, tmpfs riêng và `.git` mount read-only. Không đóng gói `.git`, secret hoặc database vào image; không khởi động/đọc DB Dev/Production. Mặc định chạy nhóm bị ảnh hưởng và Auth/native runtime; thêm `--full` để chạy toàn bộ unit/deployment tests. Log cũ tự lưu tên có timestamp trước mỗi lần chạy. Worktree có `.git` dạng file chưa được runner hỗ trợ và sẽ bị từ chối rõ ràng.

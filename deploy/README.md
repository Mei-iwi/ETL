# Docker portable ETL

Dùng tài khoản MASTER hiện có và admin riêng: [AUTH_MASTER_USERS_SETUP.md](AUTH_MASTER_USERS_SETUP.md), mode `master_users`, profile `auth`.

Đăng nhập nhiều tài khoản với Auth DB PostgreSQL riêng: xem
[AUTH_DATABASE_SETUP.md](AUTH_DATABASE_SETUP.md). Chế độ `db_users` dùng profile
`db-auth` (localhost) hoặc `db-auth-tls` (HTTPS); startup không tự tạo bảng hay tài khoản.
Giữ `auth`/`single_admin` nếu chưa thực hiện chuyển đổi có kiểm soát.

Frontend React/Nginx, FastAPI và ba worker dùng container riêng. PostgreSQL
SOURCE/Master và MongoDB dùng Compose riêng; app không tự chạy migration.

Người mới bắt đầu tại [README gốc](../README.md), theo thứ tự local → Docker.
DB Docker mới/trống: [DATABASE_SETUP.md](DATABASE_SETUP.md). Nguồn Drive:
[DRIVE_SETUP.md](DRIVE_SETUP.md). File riêng không có sau pull: docker.env, connections,
auth secrets, model/manifest và token. Không cần tải lại tài nguyên hợp lệ khi đổi code.

Launcher mặc định chọn `auth`, runtime image dùng single_admin. ADMIN_ENABLED chỉ
bật chức năng, không thay đăng nhập. `legacy` là hạ tầng khóa cũ; admin vẫn bị chặn
trong container. Với stack cũ chưa cấu hình auth, chọn legacy tường minh khi status/stop.

## Windows / Git Bash

Tạo `docker.env` ở gốc repo theo [docker.env.example](docker.env.example); file này được Git ignore. Giữ cấu hình
model/manifest/source/connection secret và network database đúng máy.
Chọn tag image mới, ví dụ:

```dotenv
BACKEND_IMAGE=etl-portable-backend:auth-20261022
FRONTEND_IMAGE=etl-portable-frontend:auth-20261022
```

Chạy từ thư mục gốc repo:

```bash
# Build trước khi có secret: legacy chỉ dùng cho bước build.
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/etl-docker.ps1 -Action Build -AuthProfile legacy \
  -EnvFile ./docker.env

# Chỉ tạo secret khi chưa có; thay YOUR_EMAIL bằng email admin thực.
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/setup-auth.ps1 \
  -Directory D:/etl/private/auth -AdminEmail YOUR_EMAIL
```

Nhập mật khẩu ít nhất 15 ký tự rồi xác nhận; màn hình không hiện ký tự khi nhập.
Script từ chối ghi đè secret đã có. Bổ sung/cập nhật các dòng cấu hình được in ra
vào docker.env, giữ các đường dẫn model/DB, tránh trùng key. Không ghi mật khẩu vào .env.
Tham khảo [.env.auth.example](.env.auth.example).

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/etl-docker.ps1 -Action Check \
  -EnvFile ./docker.env -AuthProfile auth

powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/etl-docker.ps1 -Action Start \
  -EnvFile ./docker.env -AuthProfile auth -Workers
```

Login tại `http://127.0.0.1:8080/login` nếu WEB_PORT=8080 và AUTH_ORIGIN tương ứng.
Swagger tại `/docs` sau đăng nhập; schema tại `/openapi.json`. Các asset Swagger
được đóng gói local, không cần CDN. API port8000 không publish ra host.

Nếu trang trắng và auth/config404, kiểm tra `docker ps`: có thể container vẫn dùng
image cũ. Đổi code cần build lại image và recreate container; load archive cũ
không cập nhật source mới. Archive image không chứa model, secret hoặc database.

## Linux

Chuẩn bị `docker.env` ở gốc repo trước, sau đó:

```bash
export ETL_DOCKER_ENV_FILE="$(pwd)/docker.env"
ETL_AUTH_PROFILE=legacy bash deploy/scripts/etl-docker.sh build
sudo install -d -o 10001 -g 10001 -m 0700 "$HOME/etl-private/auth"
# Tag phải khớp BACKEND_IMAGE trong docker.env (ví dụ dùng :local).
export ETL_AUTH_SETUP_IMAGE=etl-portable-backend:local
bash deploy/scripts/setup-auth.sh "$HOME/etl-private/auth" YOUR_EMAIL
# Bổ sung cấu hình được in ra vào docker.env trước khi check/start.
# Dùng root cho launcher nếu thư mục secret thuộc UID10001 và host user không đọc được.
sudo env ETL_DOCKER_ENV_FILE="$ETL_DOCKER_ENV_FILE" ETL_AUTH_PROFILE=auth bash deploy/scripts/etl-docker.sh check
sudo env ETL_DOCKER_ENV_FILE="$ETL_DOCKER_ENV_FILE" ETL_AUTH_PROFILE=auth bash deploy/scripts/etl-docker.sh start --workers
```

Hash/signing key chỉ mount cho API UID10001; proxy key dùng chung với Nginx qua
group10001. Bảo vệ thư mục private bằng ACL Windows/quyền file Linux.
Trên Linux, connections/token cần API/worker UID10001 đọc được (owner10001, mode0600);
proxy key owner10001:10001/mode0640 để frontend group10001 đọc. Không chmod777 để chữa lỗi.
Model/thư mục nguồn cần UID10001 đọc/traverse; LOCAL storage do volume quản lý. Nếu dùng
rootless Docker, kiểm tra UID mapping riêng, không coi kiểm thử Docker Desktop là nghiệm thu rootless.

## Model, đổi code và chuyển máy

Giữ tag `:local` nếu không muốn quản lý nhiều tag. Build dùng cache các lớp OCR/runtime;
đổi code vẫn cần Build rồi Start, Start dùng `--no-build`. Model được provision riêng:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action PrepareModel -AuthProfile legacy -ModelDirectory D:/etl/models/minilm -AcceptModelLicense
# Đã có model/revision: dùng manifest/verify/encode trong deploy/runtime/model.py khi cần.
```

`etl-model-manifest.json` chứa model ID, revision, 384D và SHA-256/kích thước từng file;
không sửa checksum để bỏ lỗi. Docker Check đọc manifest, VerifyModel chạy embedding offline.
Nếu đổi tag model-tool, đặt biến môi trường `ETL_MODEL_TOOL_IMAGE` cùng tag.

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Export -AuthProfile auth -EnvFile ./docker.env -Archive D:/etl/etl-images.tar
# Máy nhận: copy docker.env phù hợp máy, model/manifest và cấp secret riêng qua kênh bảo mật.
# Chuyển cả archive .tar và .tar.sha256; output Export phải là tên chưa tồn tại.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Import -AuthProfile auth -EnvFile ./docker.env -Archive D:/etl/etl-images.tar
```

Image cùng kiến trúc CPU (bộ hiện tại Linux/amd64). Máy nhận vẫn cần DB/schema/network,
model, source access và credentials; Import không chuyển dữ liệu hay tự tạo tài khoản.
Linux tương đương: `bash deploy/scripts/etl-docker.sh export /path/images.tar`, `import /path/images.tar`.

## Drive, DB khác máy và HTTPS

Drive cần overlay `-Drive` (Linux `ETL_WITH_DRIVE=true`), xem [guide](DRIVE_SETUP.md#5-docker).
Luôn dùng cùng các cờ profile/overlay cho Check/Start/Status/Logs/Stop. Không có cờ Drive
thì biến SOURCE_DRIVE trong `.env` local không tự truyền vào container.

DB remote dùng [connections.remote.example.json](config/connections.remote.example.json),
`DATABASE_MODE=remote`, `DATABASE_CA_FILE=/absolute/private/database-ca.pem` và `-RemoteDatabase`
(Linux `ETL_REMOTE_DB=true`). PostgreSQL cần `sslmode=verify-full`/CA, Mongo `tls=true`/CA;
chứng chỉ phải khớp hostname, không bật insecure. Network ngoài DB vẫn phải tồn tại theo Compose.

<a id="source-neon"></a>

### SOURCE Neon, MASTER/MongoDB local

Đặt `DATABASE_MODE=source-neon` trong `docker.env`; script PowerShell/Bash tự chọn
`compose.source-neon.yml`. Các cờ cũ vẫn hỗ trợ, không dùng chung với
`-RemoteDatabase`/`ETL_REMOTE_DB=true`. MASTER chỉ được trỏ service `postgres` hoặc
`master-postgres`, MongoDB chỉ trỏ `mongo`; SOURCE phải là hostname `*.neon.tech`.
Không đổi schema hoặc tự chạy migration.

1. Giữ URL MASTER/Mongo trong `connections.json`; thay SOURCE theo
   [mẫu này](config/connections.source-neon.example.json). Dùng role SOURCE chỉ đọc,
   không dùng `neondb_owner`. Trên Neon lấy hostname của đúng database SOURCE
   (`Learning_Resources` hoặc tên thực tế); ưu tiên kết nối trực tiếp cho worker.
2. SOURCE bắt buộc `sslmode=verify-full`, `channel_binding=require` và
   `sslrootcert=/run/secrets/source-database-ca.pem`. Trong `docker.env` đặt:

   ```dotenv
   DATABASE_MODE=source-neon
   SOURCE_DATABASE_CA_FILE=D:/etl/private/neon-ca.pem
   ```

   CA phải là trust bundle hợp lệ, không phải certificate/key tùy ý. Nếu host không
   cài Python, có thể xuất CA công khai từ Backend image đã build bằng Git Bash/Linux
   (thay tag bằng `BACKEND_IMAGE` của bạn):

   ```bash
   MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only \
     --entrypoint cat etl-portable-backend:local /etc/ssl/certs/ca-certificates.crt \
     > /d/etl/private/neon-ca.pem
   ```

3. Build image chứa runtime mới, rồi Check/Start với cùng overlay:

   ```bash
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
     -Action Build -EnvFile ./docker.env -AuthProfile auth
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
     -Action Check -EnvFile ./docker.env -AuthProfile auth
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
     -Action Start -EnvFile ./docker.env -AuthProfile auth -Workers
   ```

   Linux/Git Bash: `bash deploy/scripts/etl-docker.sh start --workers`.
   Status/Logs/Stop cũng tự đọc mode từ env; thêm Drive nếu stack dùng Drive.
   Check/Start có kết nối và kiểm tra quyền/schema DB; chỉ chạy khi được phép truy xuất nguồn.

Local dùng `.env` riêng: URL SOURCE có cùng hostname/database/reader nhưng `sslrootcert`
trỏ file CA trên host (ví dụ `D:/etl/private/neon-ca.pem`), không dùng đường dẫn container.
Giữ MASTER/Mongo local; giữ cổng `SOURCE_POSTGRES_PORT=5433` nếu còn container SOURCE cũ.
Không cần `SOURCE_MIGRATION_DATABASE_URL` để chạy ETL; restart API và ba worker sau khi đổi `.env`.

`local_demo` chỉ dùng với APP_ENVIRONMENT=local, bypass=true, public=false, origin
loopback và profile localhost được preflight xác minh. LAN dùng `auth-tls`, HTTPS,
cert/key được phê duyệt và Secure cookie; bypass=false. Không dùng cert QA cho máy thật.
LAN/production còn cần nghiệm thu network/máy thứ hai và security review.

Bằng chứng kiểm thử local nằm trong `.test-tmp/docker-portable/`. `docs/` bị ignore;
hướng dẫn này và các config dưới deploy vẫn được quản lý trong Git, không phụ thuộc
vào báo cáo local để khởi động ứng dụng.

## Các file chính

| File | Chức năng |
|---|---|
| `compose.app.yml` | API, frontend và ba worker tùy chọn; không khởi tạo database |
| `compose.database.yml` | PostgreSQL Master/SOURCE và MongoDB riêng, không chạy migration |
| `compose.auth.yml`, `compose.auth-tls.yml` | Auth secrets, session volume và HTTPS gateway |
| `compose.drive.yml`, `compose.remote-db.yml` | Credential Drive/TLS database được mount khi cần |
| `compose.ops.yml` | Bảo trì chỉ khi được owner phê duyệt, không dùng lúc app startup |
| `compose.test.yml` | Database fixture riêng, có nhãn sở hữu, dữ liệu tmpfs |
| `.env.docker.example`, `.env.auth.example` | Mẫu cấu hình không chứa secret |
| `scripts/etl-docker.ps1`, `.sh` | Build/check/start/stop và export/import image |
| `scripts/setup-auth.ps1`, `.sh` | Tạo hash và key ngoài Git, không ghi đè file cũ |
| `scripts/test-portable.ps1`, `.sh` | Kiểm thử Docker độc lập |

Backend image có Python3.12, CPU embedding dependencies, Tesseract vie+eng,
LibreOffice Writer/Impress và fonts. Host không cần cài các thành phần này.
Model MiniLM384D được mount read-only và kiểm tra revision/SHA256; không tải lúc startup.

SOURCE đọc trực tiếp, chỉ dùng file tạm theo request/job. LOCAL và Evaluation có
volume riêng; auth state dùng SQLite volume riêng, không sửa schema SOURCE/Master/Mongo.
Ba worker dùng chung Backend image; chỉ chạy khi có `-Workers` / `--workers`.

Các kiểm thử Docker đã thực hiện trên Linux/amd64 trong Docker Desktop. Không coi
kết quả đó là nghiệm thu mọi máy Windows/Linux; cần kiểm tra lại trên máy đích.

Không chạy `down -v`, prune volume/system, mount Docker socket hoặc dùng privileged
containers. Không tự chạy migration/bootstrap vào database đang có dữ liệu.
Giữ nguyên dữ liệu/model/volume khi cập nhật hoặc rollback image.

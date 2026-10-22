# ETL — Kho học liệu số

Đồng bộ metadata SOURCE → Master, OCR/trích xuất → ContentUnit/ContentBag → tìm kiếm BoW/Vector/Hybrid. Hỗ trợ PDF, DOCX, PPTX, XLSX, CSV, TXT, PNG/JPG/JPEG. Word/PPT xem dạng PDF, bảng tính xem dạng lưới; tải xuống giữ định dạng gốc.

**SOURCE chỉ lưu tham chiếu**, đọc Local/Drive khi xử lý, xem hoặc tải; chỉ dùng file tạm theo tác vụ. File **LOCAL do Master sở hữu** vẫn lưu trong `STORAGE_ROOT`.

Các lệnh chạy từ **gốc repo trong Git Bash trên Windows**. Có thể giữ nguyên tên/cổng/đường dẫn mẫu; chỉ thay email, mật khẩu và thông tin nguồn của bạn. Không có tài khoản/mật khẩu demo mặc định.

## 1. Chạy local

API, React và ba worker chạy trên host; Docker chỉ chạy database. Cần Git, **Python 3.12**, **Node.js 22.12+**, Docker Desktop (Linux containers), Tesseract `vie+eng` và LibreOffice để OCR ảnh/xem trước Office.

### 1.1. Cài dependencies

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,embeddings,google-drive]"
npm.cmd --prefix frontend ci
test -f .env || cp .env.example .env
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/storage /d/etl/models/minilm
```

Chỉ dùng Local thì có thể bỏ extra `google-drive`. Linux: dùng `source .venv/bin/activate`, `npm` thay `npm.cmd` và đổi `D:/etl/...` thành đường dẫn tuyệt đối của máy. `.env` không hỗ trợ đường dẫn `~` như shell.

### 1.2. Sửa `.env` ở gốc repo

Giữ các nhóm còn lại theo [.env.example](.env.example); cập nhật key đã có, tránh trùng. Thay ba `YOUR_*_PASSWORD` bằng mật khẩu riêng; ký tự đặc biệt trong URI phải percent-encode. File này không commit.

```dotenv
APP_ENVIRONMENT=development
ADMIN_ENABLED=true
POSTGRES_USER=etl
POSTGRES_DB=etl
POSTGRES_PASSWORD=YOUR_MASTER_PASSWORD
DATABASE_URL=postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@127.0.0.1:5432/etl
SOURCE_ADAPTER=postgresql
SOURCE_POSTGRES_USER=etl_source_admin
SOURCE_POSTGRES_DB=etl_source
SOURCE_POSTGRES_PASSWORD=YOUR_SOURCE_OWNER_PASSWORD
SOURCE_MIGRATION_DATABASE_URL=postgresql+psycopg://etl_source_admin:YOUR_SOURCE_OWNER_PASSWORD@127.0.0.1:5433/etl_source
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:YOUR_SOURCE_READER_PASSWORD@127.0.0.1:5433/etl_source
MONGO_URI=mongodb://127.0.0.1:27018
MONGO_DATABASE=etl_content
MONGO_METADATA_MODE=shared
SOURCE_MONGO_ENABLED=false
MASTER_MONGO_IDENTITY_BOOTSTRAP=false
MASTER_MONGO_IDENTITY_PROJECTION=false
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
SOURCE_FILE_STORAGE_ROOT=D:/etl/source
SOURCE_TEMP_ROOT=D:/etl/source-temp
STORAGE_ROOT=D:/etl/storage
SOURCE_DRIVE_ENABLED=false
SOURCE_DRIVE_BUCKETS={}
SEARCH_INTERNAL_DEV_ENABLED=false
INDEX_AUTO_ENABLED=false
```

| Cấu hình | Ý nghĩa/cách điền |
|---|---|
| `DATABASE_URL` | Master; role được ghi dữ liệu ứng dụng. |
| `SOURCE_DATABASE_URL` | SOURCE; **reader chỉ SELECT**, không owner/superuser. |
| `SOURCE_MIGRATION_DATABASE_URL` | Owner chỉ dùng khi chủ DB duyệt khởi tạo/migration; không truyền vào Docker app. |
| `SOURCE_FILE_STORAGE_ROOT` | Kho nguồn có sẵn; đọc `<root>/<storage_bucket>/<storage_object_key>`, không tự quét folder tạo học liệu. |
| `STORAGE_ROOT`, `SOURCE_TEMP_ROOT` | Kho LOCAL và thư mục tạm; ba thư mục này và kho nguồn không trùng/lồng nhau. |
| Hai cờ SOURCE reference/temp | Cho phép đọc nguồn/file tạm; không sao chép SOURCE lâu dài. |

DB có sẵn: dùng thông tin thực do chủ DB cấp và **bỏ bước khởi tạo**. Đổi `.env` không đổi mật khẩu role đã tồn tại.

### 1.3. Database mới, trống — khởi tạo một lần

[docker-compose.yml](docker-compose.yml) ở gốc có migration/bootstrap: chỉ chạy khi xác nhận DB local mới của bạn, không trỏ vào Dev dùng chung/Production.

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
python -m data_sync_etl.adapters.mongo.schema --check
```

Ba job khởi tạo phải kết thúc mã 0; Mongo cần `schema_ready=true`, embedding 384D. DB trống chưa có học liệu mẫu. Tạo SOURCE reader và đặt mật khẩu khớp `.env`:

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 \
  -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong `psql`: chạy `\password etl_source_reader`, nhập mật khẩu reader rồi `\q`. Nếu đổi tên DB/user mẫu, sửa cả lệnh và `GRANT CONNECT` trong SQL.

### 1.4. Đăng nhập local

```bash
python -m data_sync_etl.security.cli init --directory D:/etl/private/auth
```

Nhập/xác nhận mật khẩu **ít nhất 15 ký tự**; terminal không hiện ký tự. CLI tạo hash Argon2id và hai khóa, không ghi đè file đã có. Bổ sung vào `.env`, thay `YOUR_EMAIL` bằng email đăng nhập:

```dotenv
AUTH_MODE=single_admin
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:3000
ADMIN_LOGIN_EMAIL=YOUR_EMAIL
ADMIN_PASSWORD_HASH_FILE=D:/etl/private/auth/admin_password_hash
SESSION_SIGNING_KEY_FILE=D:/etl/private/auth/session_signing_key
AUTH_PROXY_KEY_FILE=D:/etl/private/auth/auth_proxy_key
AUTH_STATE_PATH=D:/etl/private/auth/sessions.sqlite3
```

`ADMIN_ENABLED=true` chỉ bật chức năng, không bỏ login. Vite đọc proxy key từ Backend `.env` ở phía server; không đưa secret vào `VITE_*`. Hiện xác thực một admin cấu hình sẵn, chưa dùng tài khoản trong bảng `users` của SOURCE/Master.

### 1.5. OCR, Office và model — chuẩn bị một lần

Cài [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) có English/Vietnamese và LibreOffice. Kiểm tra đường dẫn cài thực tế rồi sửa `.env`:

```dotenv
OCR_ENGINE=tesseract
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSERACT_LANGUAGES=vie+eng
OFFICE_PDF_EXECUTABLE="C:/Program Files/LibreOffice/program/soffice.exe"
INDEX_EMBEDDING_MODEL_PATH=D:/etl/models/minilm
INDEX_EMBEDDING_MODEL_ID=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
INDEX_EMBEDDING_MODEL_VERSION=e8f8c211226b894fcb81acc59f3b34ba3efd5f42
INDEX_EMBEDDING_DIMENSION=384
```

```bash
"/c/Program Files/Tesseract-OCR/tesseract.exe" --list-langs
# prepare cần Internet và chấp nhận license; bỏ qua nếu đã có đúng model.
python deploy/runtime/model.py prepare --directory D:/etl/models/minilm \
  --manifest D:/etl/models/minilm/etl-model-manifest.json --accept-model-license
python deploy/runtime/model.py encode --directory D:/etl/models/minilm \
  --manifest D:/etl/models/minilm/etl-model-manifest.json
```

Danh sách OCR cần `vie`/`eng`; `encode` kiểm tra offline/384D. Đã có model/revision nhưng thiếu JSON manifest: dùng action `manifest` thay `prepare`, rồi `encode`, không tải lại. Model/token không đi kèm khi pull code. Linux cài các gói `tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie libreoffice-writer libreoffice-impress`, chỉnh executable theo máy.

### 1.6. Khởi động hằng ngày

DB đã khởi tạo: `docker compose up -d postgres source-postgres mongo` (không chạy lại migration). Mở **5 terminal** ở repo; mỗi terminal Python kích hoạt `.venv`:

| Tiến trình | Lệnh |
|---|---|
| Backend | `python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| Frontend | `npm.cmd --prefix frontend run dev` |
| Đồng bộ metadata | `etl run-master-sync-worker --worker-id master-sync-1` |
| File nguồn | `etl run-source-file-worker --worker-id source-file-1` |
| OCR/ContentUnit/ContentBag/Index | `etl run-ocr-worker --worker-id ocr-1` |

Mở **http://127.0.0.1:3000/login**. Dùng đúng `127.0.0.1` theo `AUTH_ORIGIN`; Backend cổng 8000, Vite proxy qua 3000. Sau khi đổi `.env`, restart API/worker; đổi proxy key thì restart cả Vite. Kiểm tra Database tại **Hệ thống → Trạng thái** sau đăng nhập; `/health` trong auth mode chỉ kiểm tra API.

Nguồn cần metadata, tham chiếu, SHA-256 và trạng thái `APPROVED` hợp lệ. Đồng bộ metadata → đợi `CONTENT_READY` → xây chỉ mục tới `READY` → tìm kiếm. Có thể bật `INDEX_AUTO_ENABLED=true` sau khi kiểm tra model/schema; không tự backfill lịch sử. Dừng terminal bằng Ctrl+C, DB bằng `docker compose stop`.

**Drive tùy chọn:** làm theo [Drive setup](deploy/DRIVE_SETUP.md); mỗi người tự có token và quyền Reader. Chỉ Local thì bỏ qua. Mặc định nên giữ `single_admin` và cổng mẫu. Bỏ login chỉ cho Dev localhost cô lập: `AUTH_MODE=local_demo`, `APP_ENVIRONMENT=local`, `AUTH_DEV_ADMIN_BYPASS=true`, vẫn đủ khóa và profile localhost; `SEARCH_INTERNAL_DEV_ENABLED=false`. LAN/production với bypass bị chặn.

## 2. Build và chạy toàn bộ bằng Docker

Host chỉ cần Git và Docker Desktop/Engine + Compose v2; không cần cài Python, Node, OCR/Office/embedding runtime. Model mount read-only, không nằm trong image, không tải khi startup. API, frontend và ba worker riêng; worker dùng chung Backend image; Database có Compose riêng.

### 2.1. Cấu hình ngoài Git

```bash
mkdir -p /d/etl/private /d/etl/source /d/etl/models/minilm
test -f /d/etl/private/app.env || cp deploy/.env.docker.example /d/etl/private/app.env
test -f /d/etl/private/connections.json || cp deploy/config/connections.example.json /d/etl/private/connections.json
notepad.exe D:/etl/private/app.env
```

Trong `app.env`, cập nhật các key sau; giữ nhóm còn lại theo mẫu:

```dotenv
BACKEND_IMAGE=etl-portable-backend:local
FRONTEND_IMAGE=etl-portable-frontend:local
ETL_DATA_NETWORK=etl-data
WEB_PORT=8080
MODEL_DIR=D:/etl/models/minilm
MODEL_MANIFEST_FILE=D:/etl/models/minilm/etl-model-manifest.json
SOURCE_LOCAL_DIR=D:/etl/source
CONNECTIONS_FILE=D:/etl/private/connections.json
DATABASE_MODE=same-host
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
```

`app.env` là cấu hình Compose, khác `.env` local. `SOURCE_LOCAL_DIR` là thư mục host, container đọc tại `/mnt/source`. Local auth dùng biến `*_FILE`, Docker dùng `*_SECRET_FILE`.

Sửa `D:/etl/private/connections.json`: đúng **bốn key** theo [mẫu](deploy/config/connections.example.json), thay `CHANGE_ME` bằng credentials thực. Host phải truy cập được **từ container**, không dùng `127.0.0.1`/`localhost`. SOURCE dùng reader chỉ đọc; Mongo runtime không dùng root.

- **DB Docker đã có:** dùng network/hostname service hiện có (`docker network ls`). Ví dụ DB mục 1: `ETL_DATA_NETWORK=etl-master-data_default`, host `postgres:5432`, `source-postgres:5432`, `mongo:27017`, tên DB và role theo `.env`. Không tạo DB trống khác thay DB đang dùng.
- **DB Docker mới hoàn toàn:** xem [khởi tạo Database riêng](deploy/DATABASE_SETUP.md), gồm secrets, schema và runtime roles. `DbStart` chỉ khởi động server, chưa có schema ứng dụng.
- **DB khác máy:** cấu hình DNS/TLS, `DATABASE_MODE=remote`, thêm `-RemoteDatabase`; xem [Docker chi tiết](deploy/README.md). Không mở quyền/cổng để bỏ guard.

### 2.2. Build, chuẩn bị model và auth

```bash
# legacy CHỈ để build trước khi có auth secret; không chạy app bỏ login.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Build -AuthProfile legacy -EnvFile D:/etl/private/app.env

# Bỏ prepare nếu đã có model/manifest ở mục local; cần Internet/license khi tải.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action PrepareModel -AuthProfile legacy -ModelDirectory D:/etl/models/minilm -AcceptModelLicense

# Chỉ khi chưa có ba file secret; thay YOUR_EMAIL bằng email admin thực.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/setup-auth.ps1 \
  -Directory D:/etl/private/auth -AdminEmail YOUR_EMAIL -Image etl-portable-backend:local
```

`PrepareModel` mặc định dùng `etl-portable-backend:local`; nếu đổi tag, đặt `ETL_MODEL_TOOL_IMAGE` tương ứng. Nhập mật khẩu ít nhất 15 ký tự. Đã tạo secret ở local thì dùng lại, **không init lại**. Thêm vào `app.env`, thay email và giữ nguyên DB/model:

```dotenv
AUTH_MODE=single_admin
AUTH_APP_ENVIRONMENT=production
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:8080
ADMIN_LOGIN_EMAIL=YOUR_EMAIL
ADMIN_PASSWORD_HASH_SECRET_FILE=D:/etl/private/auth/admin_password_hash
SESSION_SIGNING_KEY_SECRET_FILE=D:/etl/private/auth/session_signing_key
AUTH_PROXY_KEY_SECRET_FILE=D:/etl/private/auth/auth_proxy_key
```

### 2.3. Kiểm tra, chạy và cập nhật

DB/schema/roles/network phải sẵn sàng. Network mới: tạo một lần bằng script `-Action Network -NetworkName etl-data`; bỏ nếu dùng network có sẵn.

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Check -AuthProfile auth -EnvFile D:/etl/private/app.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Start -AuthProfile auth -EnvFile D:/etl/private/app.env -Workers
```

Mở **http://127.0.0.1:8080/login**, Swagger **http://127.0.0.1:8080/docs** sau login. API 8000 không publish ra host. Start kiểm tra auth/model/DB, không migration; bỏ `-Workers` thì chỉ khởi động API/frontend.

**Có Drive:** cấu hình theo [Docker Drive](deploy/DRIVE_SETUP.md#5-docker), thêm `-Drive` vào Check/Start/Status/Logs/Stop; chỉ bật `.env` local không đủ. Giữ cùng overlay khi recreate.

```bash
# Đổi Status thành Logs để xem log, Stop để dừng và giữ dữ liệu.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Status -AuthProfile auth -EnvFile D:/etl/private/app.env
# Đổi source: Build rồi Start với cùng env/profile/overlay.
```

Linux dùng script Bash; xuất/nhập `.tar`, TLS và vận hành: [deploy/README.md](deploy/README.md). Archive không kèm model, secret, database hoặc học liệu. Không dùng `down -v`/prune để chữa lỗi.

## 3. Pull lại code, đổi mật khẩu và kiểm tra

- Local: dừng API/worker, kích hoạt `.venv`, chạy lại `pip install -e ".[dev,embeddings,google-drive]"` và `npm.cmd --prefix frontend ci`, đối chiếu biến mới; không chép đè `.env`. Dùng lại model/token hợp lệ.
- Docker: giữ DB/model/secrets, **Build → Start**. `docker image load` archive cũ không cập nhật source mới. Chỉ đổi config runtime thì Start để recreate.
- Đặt lại mật khẩu: `python -m data_sync_etl.security.cli hash --output D:/etl/private/auth/admin_password_hash_v2`, nhập hai lần, sửa đường dẫn hash trong `.env` hoặc `app.env`, restart Backend/recreate API. File đích phải chưa tồn tại; session cũ bị thu hồi khi API khởi động lại.
- Schema cũ cần chủ DB duyệt migration riêng. Pull/build không đồng nghĩa được phép migration DB dùng chung. Không commit `.env`, token/hash/key, dữ liệu hoặc model.

| Lỗi | Kiểm tra/sửa |
|---|---|
| `No module named argon2` | Đúng `.venv`, cài lại `python -m pip install -e ".[dev,embeddings,google-drive]"`. |
| Login trắng/auth 404 | Đúng Backend/Frontend; Docker build/recreate image mới, Ctrl+F5. |
| `SOURCE_REFERENCE_POLICY_BLOCKED` | Bật cả hai cờ reference/temp trong **app.env**, mount nguồn đúng; Drive cần `-Drive`. |
| `DATABASE_SCHEMA_NOT_READY` | Đúng DB: 15 bảng SOURCE/18 bảng Master/5 collection Mongo. DB mới theo guide, DB cũ cần owner duyệt. |
| SOURCE role bị chặn | Reader không superuser, chỉ SELECT, không quyền ghi/kế thừa quyền ghi. |
| `CONNECTIONS_SECRET_VALUES_INVALID` | JSON đủ bốn key, không còn `CHANGE_ME`, URI/role/password đúng. |
| Index/OCR lỗi | Model/revision/384D, Tesseract `vie+eng`, LibreOffice path và worker. |
| `/health` OK, DB chưa xác định | Đăng nhập và kiểm tra trên giao diện; `/api/v1/system/health` kiểm tra Master và cần quyền admin. |

Kiểm thử source dùng fixture cô lập, không DB Dev/Production:

```bash
python -m pytest tests/unit deploy/tests -q
python -m ruff check src tests deploy
node --experimental-strip-types --test frontend/tests/*.test.mjs
npm.cmd --prefix frontend run build
git diff --check
```

Chỉ báo PASS cho phép thử đã chạy thực tế. Mặc định localhost; LAN/Production cần HTTPS, `auth-tls`, mạng và phê duyệt quyền phù hợp.

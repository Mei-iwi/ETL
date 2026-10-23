# ETL — Kho học liệu số

Đồng bộ SOURCE → MASTER, OCR/trích xuất nội dung và tìm kiếm BoW/Vector/Hybrid. Tệp SOURCE được đọc trực tiếp từ nguồn; tệp LOCAL dùng storage riêng. React chạy ở cổng 3000 khi phát triển, hoặc 8080 qua Docker.

Các lệnh Windows dưới đây dùng **Git Bash tại gốc repo**, trừ khối ghi PowerShell. Linux thay `python` bằng Python 3.12, `npm.cmd` bằng `npm` và đường dẫn `D:/etl/...` bằng đường dẫn tuyệt đối Linux.

| Cấu hình | Vị trí và vai trò |
| --- | --- |
| `.env` | Tạo từ `.env.example`; API/worker local và DB Compose gốc |
| `docker.env` | Tạo từ `deploy/docker.env.example`; cấu hình ứng dụng Docker |
| `D:/etl/private/connections.json` | URL MASTER/SOURCE/Mongo cho Docker; `CONNECTIONS_FILE` trỏ tới file này |
| `D:/etl/private/auth/` | Password hash, session key và proxy key; không có mật khẩu mặc định |
| `D:/etl/models/bge-m3/` | Model offline, revision và `etl-model-manifest.json` kiểm tra checksum |

Env, secret, model và kết quả kiểm thử không đưa vào Git. Giá trị mẫu `YOUR_*` phải thay bằng giá trị thật. Biến đã export trong terminal có thể ghi đè env file.

## 1. Chạy local

### Cài thư viện

Cần Python 3.12, Node.js 22.12+, Git và Docker nếu dùng DB local trong container.

```bash
py -3.12 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,embeddings,google-drive]"
npm.cmd --prefix frontend ci
test -f .env || cp .env.example .env
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/source-temp /d/etl/storage /d/etl/models/bge-m3
```

Linux: tạo venv bằng `python3.12 -m venv .venv`, kích hoạt bằng `source .venv/bin/activate`. Không chép venv từ máy/thư mục khác.

### Cài Tesseract và LibreOffice

Tesseract đọc ảnh/PDF scan; LibreOffice chuyển Word/PPT sang PDF để xem. **PowerShell Windows:**

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact
winget install --id TheDocumentFoundation.LibreOffice --exact
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
```

Nếu thiếu `vie` hoặc `eng`, chạy PowerShell Administrator:

```powershell
foreach ($lang in @('vie', 'eng')) {
    $file = "C:\Program Files\Tesseract-OCR\tessdata\$lang.traineddata"
    if (-not (Test-Path -LiteralPath $file)) {
        Invoke-WebRequest "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$lang.traineddata" -OutFile $file
    }
}
```

Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng libreoffice-writer libreoffice-impress
tesseract --list-langs
```

### Tải model BGE-M3 1024 chiều

Chỉ tải nếu chưa có model/manifest hợp lệ; cần Internet và chấp nhận giấy phép model. Sau khi tải, runtime dùng model offline.

```bash
python deploy/runtime/model.py prepare \
  --directory D:/etl/models/bge-m3 --manifest D:/etl/models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --dimension 1024 --accept-model-license
```

### Sửa `.env`

Giữ mặc định trong `.env.example`, sửa các nhóm sau theo máy của bạn:

```dotenv
DATABASE_URL=postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@127.0.0.1:5432/etl
SOURCE_ADAPTER=postgresql
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@127.0.0.1:5433/etl_source
MONGO_URI=mongodb://127.0.0.1:27018
MONGO_DATABASE=etl_content
MONGO_METADATA_MODE=shared
SOURCE_MONGO_ENABLED=false
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
SOURCE_FILE_STORAGE_ROOT=D:/etl/source
SOURCE_TEMP_ROOT=D:/etl/source-temp
STORAGE_ROOT=D:/etl/storage
OCR_ENGINE=tesseract
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSERACT_LANGUAGES=vie+eng
OFFICE_PDF_EXECUTABLE="C:/Program Files/LibreOffice/program/soffice.exe"
INDEX_EMBEDDING_MODEL_PATH=D:/etl/models/bge-m3
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[]
SEARCH_MODEL_WARMUP_TIMEOUT_SECONDS=120
INDEX_AUTO_ENABLED=false
```

Linux dùng `/usr/bin/tesseract`, `/usr/bin/libreoffice`. URL phải percent-encode ký tự đặc biệt trong password. SOURCE runtime dùng tài khoản chỉ SELECT; không dùng owner/migration URL. Đường dẫn tệp nguồn là `<SOURCE_FILE_STORAGE_ROOT>/<bucket>/<object_key>`; tách kho SOURCE, LOCAL và thư mục tạm.

### Chuẩn bị DB

**DB đã có schema:** không chạy migration. Nếu DB local đang dừng:

```bash
docker compose up -d postgres source-postgres mongo
```

**Chỉ dành cho DB local mới/trống:** đặt `POSTGRES_USER=etl`, `POSTGRES_DB=etl`, `POSTGRES_PASSWORD`, `POSTGRES_PORT=5432`, `SOURCE_POSTGRES_USER=etl_source_admin`, `SOURCE_POSTGRES_DB=etl_source`, `SOURCE_POSTGRES_PASSWORD`, `SOURCE_POSTGRES_PORT=5433`, `MONGO_PORT=27018` trong `.env`. Sau đó:

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong psql chạy `\password etl_source_reader`, nhập password khớp URL rồi `\q`. Ba job khởi tạo phải exit 0. Không dùng nhóm lệnh này trên DB Dev/Production/remote có sẵn. DB mới chưa có người dùng hoặc học liệu nghiệp vụ.

### Cấu hình đăng nhập

Tạo secret một lần, bỏ qua nếu đã có:

```bash
python -m data_sync_etl.security.cli init --directory D:/etl/private/auth
```

Nhập password admin ít nhất 15 ký tự. Thêm vào `.env`:

```dotenv
AUTH_MODE=single_admin
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:3000
ADMIN_LOGIN_EMAIL=YOUR_ADMIN_EMAIL
ADMIN_PASSWORD_HASH_FILE=D:/etl/private/auth/admin_password_hash
SESSION_SIGNING_KEY_FILE=D:/etl/private/auth/session_signing_key
AUTH_PROXY_KEY_FILE=D:/etl/private/auth/auth_proxy_key
AUTH_STATE_PATH=D:/etl/private/auth/sessions.sqlite3
```

Đăng nhập cả user MASTER và admin cấu hình: dùng `AUTH_MODE=master_users`, `AUTH_MASTER_FRESHNESS_SECONDS=120`, `AUTH_MASTER_PBKDF2_SALT_ENCODING=auto`. Người dùng dùng mật khẩu tài khoản SOURCE đã đồng bộ, không dùng mật khẩu DB. User/role phải hợp lệ, email verified và đồng bộ users còn mới. `ADMIN_ENABLED=true` bật chức năng, không bỏ login. Xem [MASTER users](deploy/AUTH_MASTER_USERS_SETUP.md); Auth DB độc lập dùng [db_users](deploy/AUTH_DATABASE_SETUP.md).

### Khởi động

Mỗi tiến trình mở terminal riêng, kích hoạt venv cho các lệnh Python:

| Vai trò | Lệnh |
| --- | --- |
| API | `python -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| React | `npm.cmd --prefix frontend run dev` |
| Đồng bộ metadata | `python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1` |
| Xác minh/tham chiếu tệp nguồn | `python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1` |
| OCR, xử lý và lập chỉ mục | `python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1` |

Mở **http://127.0.0.1:3000/login**, Swagger admin tại **http://127.0.0.1:3000/docs**. Chạy Full baseline rồi bật Auto Incremental trong giao diện Đồng bộ, nhất là nhóm users khi dùng MASTER auth. Worker đang chạy không tự bật lịch đồng bộ. Khi model/schema đã hợp lệ, đặt `INDEX_AUTO_ENABLED=true` để tự lập chỉ mục nội dung sẵn sàng. Đổi `.env` phải restart API/worker; đổi origin/proxy key thì restart React. Dừng bằng Ctrl+C.

## 2. Chạy Docker

Host cần Git, Docker Desktop/Engine với Compose v2. Image chứa Python, Tesseract `vie+eng`, LibreOffice và embedding dependencies; model, secret và DB đặt riêng. Không cần cài Python/Node/Tesseract trên host.

### Tạo cấu hình

```bash
test -f docker.env || cp deploy/docker.env.example docker.env
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/models/bge-m3
test -f /d/etl/private/connections.json || cp deploy/config/connections.example.json /d/etl/private/connections.json
```

Sửa `docker.env`: `MODEL_DIR`, `MODEL_MANIFEST_FILE`, `CONNECTIONS_FILE`, `SOURCE_LOCAL_DIR`, `ADMIN_LOGIN_EMAIL` và ba đường dẫn `*_SECRET_FILE`. Giữ image tag `:local` nếu không cần phiên bản riêng. Bật `SOURCE_REFERENCE_ENABLED=true` và `SOURCE_TEMPORARY_FILES_ENABLED=true` khi được phép truy xuất nguồn. Mẫu dùng BGE-M3 1024D và auth localhost; muốn dùng user MASTER đặt `AUTH_MODE=master_users` như local.

Sửa `connections.json` với đúng bốn key:

```json
{
  "DATABASE_URL": "postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@postgres:5432/etl",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@source-postgres:5432/etl_source",
  "MONGO_URI": "mongodb://mongo:27017",
  "MONGO_DATABASE": "etl_content"
}
```

Docker không đọc URL DB từ `.env` local. DB container dùng hostname/alias network, không dùng `127.0.0.1`. Nếu Mongo đã bật auth, điền tài khoản/`authSource` được cấp.

### DB, network và build

DB mới: dùng nhóm khởi tạo local phía trên; DB có sẵn: bỏ migration. Nối các container DB hiện có vào network ứng dụng (tên thật lấy từ `docker ps -a`):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Network
docker network connect --alias postgres etl-data etl-master-data-postgres-1
docker network connect --alias source-postgres etl-data etl-master-data-source-postgres-1
docker network connect --alias mongo etl-data etl-master-data-mongo-1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build
```

Endpoint đã tồn tại thì bỏ bước nối tương ứng. SOURCE Neon thì không cần nối SOURCE Docker. [Compose DB riêng](deploy/DATABASE_SETUP.md) cần provision schema/roles trước; không tự migration lúc Start.

### Chuẩn bị model và secret

Có model/secret rồi thì dùng lại. Nếu chưa có, tải model bằng image vừa build và tạo secret:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action PrepareModel -ModelDirectory D:/etl/models/bge-m3 \
  -ModelId BAAI/bge-m3 -ModelRevision 5617a9f61b028005a4858fdac845db406aefb181 \
  -ModelDimension 1024 -AcceptModelLicense
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/setup-auth.ps1 \
  -Directory D:/etl/private/auth -AdminEmail YOUR_ADMIN_EMAIL -Image etl-portable-backend:local
```

Script secret không sửa `docker.env`; kiểm tra lại email và đường dẫn. Model được mount read-only và không tải lại khi khởi động.

### Start, kiểm tra, dừng và cập nhật

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -Workers
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Logs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Stop
```

`Check` kiểm tra cấu hình/model/kết nối; `Start -Workers` chạy API, frontend và cả ba worker. Mở **http://127.0.0.1:8080/login**, docs tại **http://127.0.0.1:8080/docs**.

- Đổi code: **Build → Start -Workers**.
- Đổi `docker.env`: **Start -Workers** để Compose tạo lại container cần thiết.
- Đổi nội dung secret/JSON tại cùng đường dẫn: **Stop → Check → Start -Workers**.
- Launcher dùng `-EnvFile` nếu truyền, nếu không dùng `docker.env` gốc. Kiểm tra đường dẫn env in ra. Không dùng `down -v` để sửa lỗi.

Linux dùng cùng `docker.env` với path Linux, UID 10001 phải đọc được model/secret:

```bash
bash deploy/scripts/etl-docker.sh build
bash deploy/scripts/etl-docker.sh network
bash deploy/scripts/etl-docker.sh check
bash deploy/scripts/etl-docker.sh start --workers
bash deploy/scripts/etl-docker.sh stop
```

Tải model/tạo secret trên Linux và xuất/nhập image: [hướng dẫn Docker](deploy/README.md).

## 3. Đổi DB local sang remote/Neon

| Chạy | Sửa ở đâu |
| --- | --- |
| Local | `.env`: `DATABASE_URL`, `SOURCE_DATABASE_URL`, `MONGO_URI`, `MONGO_DATABASE` |
| Docker | `connections.json`: bốn key trên; `docker.env`: `DATABASE_MODE` và đường dẫn CA |

Chỉ SOURCE Neon: giữ MASTER/Mongo local, đặt `DATABASE_MODE=source-neon`, `SOURCE_DATABASE_CA_FILE=D:/etl/private/neon-ca.pem`. SOURCE URL Docker:

```text
postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@YOUR_NEON_HOST:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=/run/secrets/source-database-ca.pem
```

Local dùng cùng URL nhưng `sslrootcert=D%3A%2Fetl%2Fprivate%2Fneon-ca.pem`. Lấy CA bundle hợp lệ từ DBA; không tắt TLS. `SOURCE_POSTGRES_*` chỉ khởi tạo DB Docker cũ, không quyết định nguồn ETL; nguồn runtime lấy từ `SOURCE_DATABASE_URL`.

Cả DB remote: đặt `DATABASE_MODE=remote`, `DATABASE_CA_FILE` và URL TLS theo [mẫu remote](deploy/config/connections.remote.example.json). Không chạy migration/seed lên DB remote. Đổi kết nối xong restart local; Docker chạy Stop → Check → Start -Workers.

## 4. Kiểm tra và cấu hình tùy chọn

- `AUTH_MASTER_STALE`: kiểm tra Full baseline/Auto users và Master Sync Worker; không bỏ freshness guard.
- Search rỗng: nội dung phải sẵn sàng, chỉ mục READY, đúng model/dimension và quyền. `SEARCH_VECTOR_MIN_SIMILARITY=0.6`; giảm ngưỡng có thể tăng nhiễu. `SEARCH_MEANING_CONTEXT_MARGIN=0.08` chọn đoạn gần nghĩa; `SEARCH_BOW_MIN_COVERAGE=0` giữ recall mặc định.
- Vector 384D cũ không dùng lại như 1024D; chuyển model cần backup và tái lập chỉ mục theo công cụ maintenance, không chỉ đổi env.
- File nguồn cần quyền, metadata/hash hợp lệ và mount read-only; [Google Drive](deploy/DRIVE_SETUP.md) cần cấu hình riêng.
- Quên password admin: `python -m data_sync_etl.security.cli hash --output D:/etl/private/auth/admin_password_hash_v2`, trỏ secret sang file mới rồi restart. Password user MASTER đổi ở SOURCE.

Kiểm thử fixture, không dùng DB đang phục vụ:

```bash
python -m pytest tests/unit -q
npm.cmd --prefix frontend run build
```

Scripts đánh giá và dữ liệu nhãn: [tìm kiếm](research/semantic-quality/README.md). Kết quả sinh mới lưu trong `.test-tmp` hoặc `storage`, không đưa vào Git.

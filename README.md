# ETL — Kho học liệu số

Đồng bộ SOURCE → MASTER, OCR/trích xuất nội dung và tìm kiếm BoW, BGE-M3 Vector, Hybrid. Tệp SOURCE được đọc trực tiếp từ nguồn; tệp LOCAL dùng storage riêng. Backend FastAPI, frontend React/TypeScript, PostgreSQL và MongoDB.

Các lệnh Windows dùng **Git Bash tại gốc repo**, trừ khối ghi PowerShell. Linux dùng `npm` thay `npm.cmd` và thay `D:/etl/...` bằng đường dẫn tuyệt đối Linux. Không dùng dấu backtick PowerShell để xuống dòng trong Git Bash.

| Cấu hình | Vị trí và vai trò |
| --- | --- |
| `.env` ← `.env.example` | API/worker local và Compose DB ở gốc repo |
| `docker.env` ← `deploy/docker.env.example` | Image, mount, auth và chế độ kết nối Docker ứng dụng |
| `D:/etl/private/connections.json` | Bốn thông số kết nối DB của Docker |
| `D:/etl/private/auth/` | Password hash, session key, proxy key; không có mật khẩu mặc định |
| `D:/etl/models/bge-m3/` | Model offline và manifest kiểm tra checksum |

Env thật, secret, model và báo cáo chạy thử không đưa vào Git. Thay `YOUR_*` bằng giá trị thật. Biến đã export trong terminal có thể ghi đè env file.

## 1. Chạy local

### 1.1. Cài thư viện

Cần Python 3.12, Node.js 22.12+, Git; Docker chỉ cần nếu chạy DB trong container.

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

### 1.2. Tesseract và LibreOffice

Tesseract đọc ảnh/PDF scan; LibreOffice chuyển Word/PPT sang PDF để xem. **PowerShell Windows:**

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact
winget install --id TheDocumentFoundation.LibreOffice --exact
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
```

Nếu thiếu `vie` hoặc `eng`, chạy **PowerShell Administrator**:

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

### 1.3. Chuẩn bị BGE-M3 1024 chiều

Tải một lần nếu chưa có model/manifest hợp lệ; cần Internet và chấp nhận giấy phép model. Runtime không tải lại.

```bash
python deploy/runtime/model.py prepare \
  --directory D:/etl/models/bge-m3 --manifest D:/etl/models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --dimension 1024 --accept-model-license
```

### 1.4. Sửa `.env`

Giữ các giá trị còn lại từ `.env.example`; sửa URL, password và đường dẫn theo máy. Mẫu dùng DB local:

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

Linux dùng `/usr/bin/tesseract`, `/usr/bin/libreoffice`. Password trong URL phải percent-encode ký tự đặc biệt. SOURCE runtime dùng tài khoản chỉ SELECT. Tệp nguồn local nằm ở `<SOURCE_FILE_STORAGE_ROOT>/<bucket>/<object_key>`; kho SOURCE, LOCAL và thư mục tạm tách riêng. [Nguồn Google Drive](deploy/DRIVE_SETUP.md) cần credentials/quyền riêng.

### 1.5. Chuẩn bị DB

**DB có schema sẵn:** điền URL ở trên, không chạy migration. Nếu DB local đã tạo nhưng đang dừng:

```bash
docker compose up -d postgres source-postgres mongo
```

**Chỉ dành cho DB local mới/trống:** thêm vào `.env` các giá trị sau; password MASTER phải khớp `DATABASE_URL`. Password owner SOURCE khác password reader.

```dotenv
POSTGRES_USER=etl
POSTGRES_DB=etl
POSTGRES_PASSWORD=YOUR_MASTER_PASSWORD
POSTGRES_PORT=5432
SOURCE_POSTGRES_USER=etl_source_admin
SOURCE_POSTGRES_DB=etl_source
SOURCE_POSTGRES_PASSWORD=YOUR_SOURCE_OWNER_PASSWORD
SOURCE_POSTGRES_PORT=5433
MONGO_PORT=27018
```

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong psql chạy `\password etl_source_reader`, nhập password khớp `SOURCE_DATABASE_URL`, rồi `\q`. Ba job khởi tạo phải exit 0. DB mới chưa có người dùng/học liệu. Không chạy nhóm khởi tạo này trên Dev/Production hoặc remote có sẵn.

### 1.6. Đăng nhập

Tạo secret một lần, bỏ qua nếu đã có; nhập password admin ít nhất 15 ký tự:

```bash
python -m data_sync_etl.security.cli init --directory D:/etl/private/auth
```

Thêm vào `.env`:

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

| Nhu cầu | Cấu hình |
| --- | --- |
| Chỉ admin cấu hình riêng | `AUTH_MODE=single_admin` |
| User MASTER và admin riêng | `AUTH_MODE=master_users`, `AUTH_MASTER_FRESHNESS_SECONDS=120`, `AUTH_MASTER_PBKDF2_SALT_ENCODING=auto` |
| Auth DB độc lập | `AUTH_MODE=db_users`; theo [hướng dẫn Auth DB](deploy/AUTH_DATABASE_SETUP.md) |

User MASTER dùng mật khẩu tài khoản SOURCE đã đồng bộ, không dùng password DB. User/email verified/role phải hợp lệ và đồng bộ users còn mới. `ADMIN_ENABLED=true` bật chức năng, không bỏ login. Chi tiết: [MASTER users](deploy/AUTH_MASTER_USERS_SETUP.md).

### 1.7. Khởi động

Mỗi tiến trình mở terminal riêng; kích hoạt venv trước lệnh Python:

| Tiến trình | Lệnh |
| --- | --- |
| API | `python -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| React | `npm.cmd --prefix frontend run dev` |
| Đồng bộ metadata | `python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1` |
| Xác minh/tham chiếu tệp | `python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1` |
| OCR/xử lý nội dung | `python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1` |

Mở **http://127.0.0.1:3000/login**; Swagger admin qua **http://127.0.0.1:3000/docs**. Chạy Full baseline rồi bật Auto Incremental trong giao diện Đồng bộ, đặc biệt luồng users khi dùng MASTER auth. Worker đang chạy không tự bật lịch đồng bộ.

Khi model/schema/index hợp lệ, đặt `INDEX_AUTO_ENABLED=true` để tự lập chỉ mục. Đổi `.env` phải restart API/worker; đổi origin/proxy key phải restart React. Dừng bằng Ctrl+C.

## 2. Chạy Docker

Host cần Git, Docker Desktop/Engine và Compose v2. Image chứa Python, Tesseract `vie+eng`, LibreOffice và embedding dependencies. Model, secret và DB nằm riêng; host không cần Python/Node/Tesseract.

### 2.1. Cấu hình

```bash
test -f docker.env || cp deploy/docker.env.example docker.env
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/models/bge-m3
test -f /d/etl/private/connections.json || cp deploy/config/connections.example.json /d/etl/private/connections.json
```

Sửa `docker.env` ở gốc repo, tối thiểu:

```dotenv
BACKEND_IMAGE=etl-portable-backend:local
FRONTEND_IMAGE=etl-portable-frontend:local
WEB_PORT=8080
MODEL_DIR=D:/etl/models/bge-m3
MODEL_MANIFEST_FILE=D:/etl/models/bge-m3/etl-model-manifest.json
CONNECTIONS_FILE=D:/etl/private/connections.json
SOURCE_LOCAL_DIR=D:/etl/source
DATABASE_MODE=same-host
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
AUTH_MODE=single_admin
ADMIN_LOGIN_EMAIL=YOUR_ADMIN_EMAIL
ADMIN_PASSWORD_HASH_SECRET_FILE=D:/etl/private/auth/admin_password_hash
SESSION_SIGNING_KEY_SECRET_FILE=D:/etl/private/auth/session_signing_key
AUTH_PROXY_KEY_SECRET_FILE=D:/etl/private/auth/auth_proxy_key
```

Giữ BGE-M3 1024D và auth localhost theo template; dùng user MASTER thì đổi `AUTH_MODE=master_users`. Hai cờ SOURCE chỉ bật khi được phép truy xuất nguồn. Nếu đổi `WEB_PORT`, đổi cả `AUTH_ORIGIN` (mặc định `http://127.0.0.1:8080`). LAN/HTTPS cần [cấu hình proxy/TLS](deploy/README.md), không bật bypass.

Sửa `connections.json` với đúng bốn key:

```json
{
  "DATABASE_URL": "postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@postgres:5432/etl",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@source-postgres:5432/etl_source",
  "MONGO_URI": "mongodb://mongo:27017",
  "MONGO_DATABASE": "etl_content"
}
```

Docker ứng dụng lấy URL từ file này, không dùng URL của `.env` local. DB cùng network dùng alias/cổng nội bộ; `127.0.0.1` trong container là chính container. Mongo có auth cần credentials/`authSource` được cấp.

### 2.2. DB, network và build

Chuẩn bị DB theo mục 1.5 hoặc [Compose DB riêng](deploy/DATABASE_SETUP.md); Start không tự migrate. Với DB của Compose gốc, nối network một lần (tên thật lấy từ `docker ps -a`):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Network
docker network connect --alias postgres etl-data etl-master-data-postgres-1
docker network connect --alias source-postgres etl-data etl-master-data-source-postgres-1
docker network connect --alias mongo etl-data etl-master-data-mongo-1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build
```

Bỏ lệnh connect nếu endpoint đã tồn tại. SOURCE Neon không cần container SOURCE. Ba worker dùng chung backend image, không build riêng.

### 2.3. Model và secret

Có model/manifest và secret từ local thì dùng lại. Nếu chưa có, sau khi build chạy:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action PrepareModel -ModelDirectory D:/etl/models/bge-m3 \
  -ModelId BAAI/bge-m3 -ModelRevision 5617a9f61b028005a4858fdac845db406aefb181 \
  -ModelDimension 1024 -AcceptModelLicense
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/setup-auth.ps1 \
  -Directory D:/etl/private/auth -AdminEmail YOUR_ADMIN_EMAIL -Image etl-portable-backend:local
```

Script secret không sửa `docker.env`; kiểm tra email/đường dẫn. Model mount read-only. `PrepareModel` dùng image `etl-portable-backend:local`; nếu đổi tag đặt `ETL_MODEL_TOOL_IMAGE` theo image đã build.

### 2.4. Start, kiểm tra và cập nhật

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -Workers
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Logs
```

`Check` kiểm tra auth/model/DB; `Start -Workers` chạy API, frontend và ba worker. Mở **http://127.0.0.1:8080/login**; Swagger admin tại **http://127.0.0.1:8080/docs**.

| Thao tác | Nhóm lệnh `-Action` |
| --- | --- |
| Đổi code, cập nhật image/container | `Build` → `Start -Workers` |
| Đổi `docker.env` | `Start -Workers` để tạo lại container cần thiết |
| Đổi nội dung secret/JSON cùng đường dẫn | `Stop` → `Check` → `Start -Workers` |
| Dừng, giữ dữ liệu | `Stop` |

Launcher mặc định đọc `docker.env` ở gốc repo. Dùng `-EnvFile D:/etl/private/app.env` cho mọi lệnh nếu chọn vị trí khác. Không dùng `down -v` để sửa lỗi.

Linux dùng đường dẫn Linux trong `docker.env`; UID 10001 phải đọc được model/secret:

```bash
bash deploy/scripts/etl-docker.sh network
bash deploy/scripts/etl-docker.sh build
bash deploy/scripts/etl-docker.sh check
bash deploy/scripts/etl-docker.sh start --workers
bash deploy/scripts/etl-docker.sh stop
```

Model/secret Linux và xuất/nhập image `.tar`: [hướng dẫn Docker](deploy/README.md). Archive image không chứa model, secret hoặc dữ liệu DB.

## 3. Đổi DB sang remote/Neon

| Chạy | File cần sửa |
| --- | --- |
| Local | `.env`: `DATABASE_URL`, `SOURCE_DATABASE_URL`, `MONGO_URI`, `MONGO_DATABASE` |
| Docker | Bốn key trên trong `connections.json`; `docker.env`: `DATABASE_MODE`, đường dẫn CA |

**Chỉ SOURCE Neon:** giữ MASTER/Mongo local, đổi `SOURCE_DATABASE_URL`. Docker đặt `DATABASE_MODE=source-neon`, `SOURCE_DATABASE_CA_FILE=D:/etl/private/neon-ca.pem`. URL SOURCE Docker:

```text
postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@YOUR_NEON_HOST:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=/run/secrets/source-database-ca.pem
```

Local thay `sslrootcert` bằng `D%3A%2Fetl%2Fprivate%2Fneon-ca.pem`. Dùng đúng hostname, tên DB phân biệt hoa/thường, role chỉ đọc và CA bundle được DBA cấp. `SOURCE_POSTGRES_*` chỉ tạo DB Docker local, không chọn nguồn ETL; runtime dùng `SOURCE_DATABASE_URL`. Bỏ `SOURCE_MIGRATION_DATABASE_URL` nếu không chủ ý migrate DB nguồn local.

**Các DB remote:** đặt `DATABASE_MODE=remote`, `DATABASE_CA_FILE` và URL TLS theo [mẫu remote](deploy/config/connections.remote.example.json). Không chạy migration/seed lên remote. Sau thay đổi restart API/worker local; Docker chạy `Stop` → `Check` → `Start -Workers`.

## 4. Kiểm tra và xử lý lỗi

| Hiện tượng | Kiểm tra |
| --- | --- |
| `AUTH_MASTER_STALE` | Full baseline/Auto users và Master Sync Worker; không bỏ freshness guard |
| Search rỗng | Nội dung/index READY, quyền, model/revision/dimension khớp; không hạ ngưỡng về 0 để che lỗi |
| `SOURCE_REFERENCE_DISABLED` / không xem tệp | Hai cờ SOURCE, quyền nguồn, mount/credentials và metadata/hash |
| Container unhealthy / BLOCKED | `-Action Logs`, `-Action Check`; đọc mã lỗi cụ thể trước khi sửa |
| Thiếu `argon2` / launcher trỏ đường dẫn cũ | Đúng venv và dependencies mục 1.1; tạo venv mới nếu đã di chuyển repo |

Vector 384D không dùng lại như 1024D: đổi model cần backup, điều chỉnh validator và tái lập chỉ mục theo quy trình maintenance. Semantic Chunk và các tùy chọn fusion/evidence vẫn opt-in; giữ mặc định nếu chưa kiểm chứng.

Thử nghiệm query không dấu: `SEARCH_APPROVED_ACCENT_VARIANTS_ENABLED=true` trong `.env` local hoặc `docker.env`, rồi restart API/tạo lại container. Cờ mặc định `false`; chỉ dùng cụm tiếng Việt đã duyệt, giữ query gốc và thêm tối đa một biến thể. Không cần reindex; không bật cùng ColBERT. Đây chưa phải cấu hình được nghiệm thu chất lượng production; rollback bằng `false`. Export lexical V1 từ chối chế độ này để tránh báo parity sai.

Metadata search là nhánh riêng cho tiêu đề MASTER và mô tả/tags Mongo liên kết: `SEARCH_METADATA_ENABLED=true`, `SEARCH_METADATA_CONTENT_ENABLED=true`; MP3/MP4 đăng ký metadata dùng `SOURCE_METADATA_MEDIA_ENABLED=true`. Cần collection chỉ mục được cấp phép; bật cờ không tự tạo schema. Vector metadata dùng adapter BGE-M3/FlagEmbedding: `SEMANTIC_MODEL_PATH` trỏ đến checkpoint local và `SEMANTIC_MODEL_REVISION` khớp file `etl-model-revision.txt`; không bắt buộc bật `SEMANTIC_ENABLED` cho content. Luồng này không trích xuất âm thanh/video.

Sau khi chỉ mục đã sẵn sàng và được phép ghi, có thể bật `SEARCH_METADATA_AUTO_REFRESH_ENABLED=true` trong `.env`/`docker.env` và chạy **Master Sync Worker** hiện có; thêm `SEARCH_METADATA_REFRESH_DENSE=true` nếu cần cập nhật vector. Worker quét mặc định 5 học liệu mỗi 30 giây, lưu checkpoint và retry; không OCR lại. Hai cờ tự động mặc định `false`. Truy vấn metadata toàn kho vẫn có giới hạn 200 ứng viên; dữ liệu chưa lập chỉ mục hoặc lỗi nhánh được báo partial. Chưa nghiệm thu chất lượng production trên bộ truy vấn có đáp án chuẩn.

Đối soát **Kho tệp** chạy nền: API trả `202`, giao diện cập nhật tiến độ từng tệp mỗi 2 giây; có thể rời trang mà không hủy lượt quét. `SOURCE_READ_TIMEOUT_SECONDS` (mặc định `120`) giới hạn riêng từng tệp, không giới hạn cả kho trong 30 giây. Tệp lỗi/hết thời gian được ghi **Chưa xác minh**, các tệp khác vẫn tiếp tục. Nếu API bị dừng, mở lại trang và bấm **Quét đối soát** để ưu tiên phần chưa quét; không tái sử dụng hash cũ làm kết quả xác minh mới. Chỉ ghi nhật ký đối soát, không sửa dữ liệu học liệu hoặc lưu bản sao SOURCE lâu dài.

Đổi password admin cấu hình: `python -m data_sync_etl.security.cli hash --output D:/etl/private/auth/admin_password_hash_v2`, đổi đường dẫn secret rồi restart. Password user MASTER đổi ở SOURCE rồi đồng bộ.

Kiểm thử fixture, không cần DB đang phục vụ:

```bash
python -m pytest tests/unit -q
node --experimental-strip-types --test --test-concurrency=1 frontend/tests/*.test.mjs
npm.cmd --prefix frontend run build
```

Hợp đồng schema ở `fixtures/contracts/`; truy vấn/nhãn đánh giá ở `fixtures/search/`. Nhãn draft chưa được nghiệm thu. Báo cáo sinh mới đặt trong `.test-tmp/` hoặc `artifacts/`; `docs/` và `research/` chỉ giữ ở máy, không thuộc cây source Git.

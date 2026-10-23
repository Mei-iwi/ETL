# ETL — Kho học liệu số

SOURCE → MASTER → OCR/trích xuất → ContentUnit/ContentBag → tìm kiếm BoW, Vector và Hybrid.
Tệp SOURCE chỉ lưu tham chiếu, đọc từ nguồn khi xử lý/xem/tải; tệp LOCAL dùng kho riêng của MASTER.
Word/PowerPoint xem dạng PDF; XLSX/CSV xem dạng lưới; tải xuống giữ định dạng gốc.

Hướng dẫn dùng **BGE-M3 dense 1024 chiều**, chạy lệnh từ **gốc repo bằng Git Bash trên Windows**, trừ khối ghi rõ PowerShell/Linux.
Giữ tên/cổng/đường dẫn mẫu nếu không cần khác biệt; thay `YOUR_*` bằng thông tin thật. Không có mật khẩu đăng nhập mặc định.

| Cách chạy | Cần cài trên host | Giao diện |
| --- | --- | --- |
| Local | Python3.12, Node.js22.12+, Tesseract, LibreOffice; Docker nếu chạy DB local | http://127.0.0.1:3000/login |
| Docker ứng dụng | Git, Docker Desktop/Engine + Compose v2; model và secret cấp riêng | http://127.0.0.1:8080/login |

| File cấu hình | Được dùng ở đâu |
| --- | --- |
| `.env` gốc repo | Backend/worker local; Compose DB ở gốc dùng nhóm `POSTGRES_*` |
| `docker.env` gốc repo | Script Docker: image/model, topology DB, đường dẫn secret, auth |
| JSON tại `CONNECTIONS_FILE`, ví dụ `D:/etl/private/connections.json` | URL MASTER/SOURCE/Mongo thật cho API và worker Docker |

Hai file env được Git ignore; JSON secret giữ ngoài repo. Docker app không lấy URL từ `.env` host.
Biến đã export trong terminal có thể ghi đè env file; bỏ giá trị export cũ khi kiểm tra cấu hình.

## 1. Chạy local

### 1.1. Cài dependencies

Cài [Git](https://git-scm.com/downloads), [Python3.12](https://www.python.org/downloads/),
[Node.js LTS](https://nodejs.org/en/download) và [Docker Desktop](https://docs.docker.com/desktop/) trước;
Docker chọn Linux containers. Mở terminal mới sau khi cài, kiểm tra:

```bash
py -3.12 --version
node --version
npm.cmd --version
docker compose version
```

Sau đó tạo môi trường và cài thư viện:

```bash
py -3.12 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,embeddings,google-drive]"
npm.cmd --prefix frontend ci
test -f .env || cp .env.example .env
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/source-temp /d/etl/storage /d/etl/models/bge-m3
```

Linux: `python3.12 -m venv .venv`, `source .venv/bin/activate`, dùng `npm` thay `npm.cmd`.
Trong env thay `D:/etl/...` bằng đường dẫn tuyệt đối thực, ví dụ `/home/your-user/etl/...`; không dùng `~`.
Không chép `.venv` từ máy/thư mục khác. Nếu launcher `pip.exe`, `hf.exe`, `etl.exe` trỏ đường dẫn cũ, tạo lại venv; dùng `python -m ...`.

### 1.2. Cài Tesseract và LibreOffice

**Windows — PowerShell:**

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact
winget install --id TheDocumentFoundation.LibreOffice --exact
```

Tesseract cần dữ liệu cả tiếng Việt/Anh. Nếu thiếu `vie` hoặc `eng`, mở **PowerShell Run as administrator**:

```powershell
$tessdata = 'C:\Program Files\Tesseract-OCR\tessdata'
foreach ($language in @('eng', 'vie')) {
    $target = Join-Path $tessdata ($language + '.traineddata')
    if (-not (Test-Path -LiteralPath $target)) {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$language.traineddata" -OutFile $target
    }
}
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
& 'C:\Program Files\LibreOffice\program\soffice.exe' --version
```

Kết quả phải có `eng`/`vie`. Đổi đường dẫn nếu cài ở vị trí khác.
Nguồn: [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html),
[Windows installer](https://github.com/UB-Mannheim/tesseract/wiki), [LibreOffice](https://www.libreoffice.org/installation-instructions/).

**Ubuntu/Debian:**

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie \
  libreoffice-writer libreoffice-impress fonts-dejavu-core fonts-liberation
tesseract --list-langs
libreoffice --version
```

### 1.3. Tải model một lần

Đặt model tại **`D:/etl/models/bge-m3`**, ngoài repo. Đã có model ở chỗ khác thì dùng đường dẫn đó, không cần tải lại.
Trong Git Bash có `.venv`:

```bash
python deploy/runtime/model.py prepare \
  --directory D:/etl/models/bge-m3 \
  --manifest D:/etl/models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 \
  --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --dimension 1024 --accept-model-license
```

Lệnh cần Internet/license, tải model và tạo revision/manifest checksum. Kiểm tra nạp offline:

```bash
python deploy/runtime/model.py encode \
  --directory D:/etl/models/bge-m3 \
  --manifest D:/etl/models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 \
  --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
```

Cần `"status":"PASS"` (có thể có khoảng trắng) và `"dimension":1024`.
Thư mục chứa trọng số/tokenizer, `modules.json`, `etl-model-revision.txt`, `etl-model-manifest.json`.
Manifest ghi ID/revision/dimension và checksum, không phải trọng số. Công cụ model **không đọc `.env`**, vì vậy truyền đủ tham số.
Có đúng model/manifest thì chỉ `encode`; ứng dụng chạy offline, không tự tải lại.

### 1.4. Điền `.env` gốc repo

Mở `notepad.exe .env`; **sửa key đã có, tránh trùng dòng**. Mẫu local với DB Docker cùng máy:

```dotenv
APP_ENVIRONMENT=development
ADMIN_ENABLED=true
POSTGRES_USER=etl
POSTGRES_DB=etl
POSTGRES_PASSWORD=YOUR_MASTER_PASSWORD
POSTGRES_PORT=5432
DATABASE_URL=postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@127.0.0.1:5432/etl

SOURCE_ADAPTER=postgresql
SOURCE_POSTGRES_USER=etl_source_admin
SOURCE_POSTGRES_DB=etl_source
SOURCE_POSTGRES_PASSWORD=YOUR_SOURCE_OWNER_PASSWORD
SOURCE_POSTGRES_PORT=5433
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@127.0.0.1:5433/etl_source

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

OCR_ENGINE=tesseract
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSERACT_LANGUAGES=vie+eng
OFFICE_PDF_EXECUTABLE="C:/Program Files/LibreOffice/program/soffice.exe"

INDEX_EMBEDDING_MODEL_PATH=D:/etl/models/bge-m3
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[]
INDEX_AUTO_ENABLED=false
SEARCH_MODEL_WARMUP_TIMEOUT_SECONDS=120
MASTER_SYNC_AUTO_INTERVAL_SECONDS=30
SEARCH_INTERNAL_DEV_ENABLED=false
```

Linux dùng `/usr/bin/tesseract`, `/usr/bin/libreoffice` và đường dẫn host thực.
Ký tự đặc biệt trong password phải percent-encode **trong URL**, còn `POSTGRES_PASSWORD` giữ mật khẩu gốc.
SOURCE dùng **reader chỉ SELECT**, không owner. `SOURCE_POSTGRES_*` chỉ khởi tạo container local, không chọn nguồn ETL.
Bỏ/comment `SOURCE_MIGRATION_DATABASE_URL` khi vận hành, nhất là dùng remote; chỉ owner dùng biến này khi được duyệt migration.
Kho nguồn đọc `<root>/<storage_bucket>/<storage_object_key>` theo metadata; không tự nhập mọi tệp trong folder.
Kho SOURCE, LOCAL và thư mục tạm không được trùng hoặc lồng nhau.

### 1.5. Database có sẵn hoặc khởi tạo mới

**Đã có schema/dữ liệu:** sửa URL rồi bỏ bước khởi tạo. DB local chỉ cần:

```bash
docker compose up -d postgres source-postgres mongo
```

**Chỉ DB local mới/trống của riêng bạn:** đặt dimension1024 trong `.env` trước lần đầu, rồi:

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
```

Compose gốc chạy migration/bootstrap; ba job phải kết thúc mã0. Không chạy bước này trên DB Dev dùng chung/Production.
DB mới chưa có học liệu/tài khoản nghiệp vụ; kết nối được chưa có nghĩa tìm kiếm có dữ liệu.
Tạo SOURCE reader local và nhập mật khẩu khớp `.env`:

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 \
  -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong `psql`: `\password etl_source_reader`, nhập hai lần, rồi `\q`. Script xử lý role tồn tại, không cần xóa role.
Nếu đổi DB/user mẫu, sửa tham số và `GRANT CONNECT` trong SQL trước khi owner chạy.
Kiểm tra Mongo chỉ đọc:

```bash
python -m data_sync_etl.adapters.mongo.schema --check
```

Cần `schema_ready=true`. Thuần1024 dùng `INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[]`.
Đã có vector384 thì xem mục4.2, không sửa validator để bỏ lỗi.

### 1.6. Admin và tài khoản MASTER

Tạo secret **một lần**; bỏ qua nếu đã có đủ ba file:

```bash
python -m data_sync_etl.security.cli init --directory D:/etl/private/auth
```

Nhập/xác nhận mật khẩu ít nhất **15 ký tự**; terminal không hiện ký tự.
Tạo `admin_password_hash` (Argon2id), `session_signing_key`, `auth_proxy_key`; không lưu mật khẩu gốc để xem lại.
Trong `.env`:

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

| Mode | Tài khoản |
| --- | --- |
| `single_admin` | Email cấu hình + mật khẩu vừa đặt; phù hợp cài mới chưa có users SOURCE |
| `master_users` | User MASTER đã đồng bộ và admin riêng nếu bật cờ |
| `db_users` | Auth DB/RBAC độc lập: [hướng dẫn riêng](deploy/AUTH_DATABASE_SETUP.md) |

Dùng **cả user MASTER và admin**: giữ secret trên, đổi/thêm:

```dotenv
AUTH_MODE=master_users
AUTH_MASTER_FRESHNESS_SECONDS=120
AUTH_MASTER_PBKDF2_SALT_ENCODING=auto
```

User nhập mật khẩu tài khoản SOURCE đã đồng bộ, **không phải password PostgreSQL**.
Account/role phải active, email verified, chưa xóa/thu hồi; users sync COMPLETED trong120giây gần nhất.
MASTER `role_code=ADMIN` nhận quyền admin khi `ADMIN_ENABLED=true`; role khác là Viewer.
Admin riêng dùng email/mật khẩu secret; form không có dropdown. Cờ admin không bỏ login.
Chi tiết: [MASTER users](deploy/AUTH_MASTER_USERS_SETUP.md).

Quên/đổi password admin: tạo hash mới, sửa đường dẫn rồi restart API:

```bash
python -m data_sync_etl.security.cli hash --output D:/etl/private/auth/admin_password_hash_v2
```

File đích phải mới; không ghi đè secret đang dùng. User MASTER đổi password tại SOURCE rồi chờ đồng bộ.
Bypass chỉ cho `local_demo` trên localhost dev cô lập, không bật trong Docker production/LAN.

### 1.7. Khởi động hằng ngày

Mở **5 terminal** tại gốc repo; mỗi terminal Python kích hoạt `.venv` trước:

| Terminal | Lệnh |
| --- | --- |
| API | `python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| React | `npm.cmd --prefix frontend run dev` |
| Master Sync | `python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1` |
| Source File | `python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1` |
| OCR/ContentUnit/ContentBag/Index | `python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1` |

Mở **http://127.0.0.1:3000/login**; Swagger admin qua **http://127.0.0.1:3000/docs**.
API cổng8000; `/health` chỉ báo API sống, không xác nhận DB/model/pipeline.
Trong admin → Kho học liệu → Đồng bộ dữ liệu nguồn: chạy Full baseline, sau đó bật Auto Incremental,
đặc biệt nhóm `users` khi dùng `master_users`. Chỉ chạy worker chưa tự bật lịch.
`MASTER_SYNC_AUTO_INTERVAL_SECONDS=30` là giá trị mặc định khi tạo cấu hình mới; lịch đã lưu trong DB cần kiểm tra tại trang đồng bộ.

Metadata/phiên bản → đọc nguồn/OCR → CONTENT_READY → lập chỉ mục READY → tìm kiếm.
Model/schema hợp lệ rồi có thể bật `INDEX_AUTO_ENABLED=true`, restart API/OCR Worker.
Cờ này xếp hàng nội dung vừa xử lý xong, không tự xây lại mọi chỉ mục cũ/thất bại; dùng Tái lập chỉ mục cho các phiên bản đó.
Sửa `.env`: restart **API và cả ba worker**; đổi proxy key/origin thì restart Vite.
`--reload` không bảo đảm worker nạp lại env. Dừng terminal bằng Ctrl+C; DB bằng `docker compose stop`.

### 1.8. Local đổi DB remote / SOURCE Neon

Không sửa code. Chỉ DB nào đổi máy thì thay URL DB đó trong `.env`; giữ các URL còn lại:

```dotenv
DATABASE_URL=postgresql+psycopg://MASTER_USER:ENCODED_PASSWORD@master-db.example.com:5432/etl?sslmode=verify-full&sslrootcert=D:/etl/private/database-ca.pem
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@source-db.example.com:5432/Learning_Resources?sslmode=verify-full&sslrootcert=D:/etl/private/database-ca.pem
MONGO_URI=mongodb://MONGO_USER:ENCODED_PASSWORD@mongo-db.example.com:27017/etl_content?authSource=etl_content&tls=true&tlsCAFile=D:/etl/private/database-ca.pem
MONGO_DATABASE=etl_content
```

CA do đơn vị vận hành cấp hoặc trust bundle phù hợp; giữ hostname DNS khớp chứng chỉ, không tắt xác minh TLS để chữa lỗi.
`/Learning_Resources` là tên **database**, không phải schema PostgreSQL.

**Chỉ SOURCE chuyển Neon:** giữ MASTER/Mongo local, thay riêng:

```dotenv
SOURCE_ADAPTER=postgresql
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@ep-YOUR-ENDPOINT.REGION.aws.neon.tech:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=D:/etl/private/neon-ca.pem
```

Lấy host/database tại Neon → Connect; worker có thể dùng host trực tiếp không `-pooler`.
App dùng reader được cấp quyền, không dùng `neondb_owner`. DBA cấp reader trên **đúng DB** theo [source_reader.sql](scripts/sql/source_reader.sql),
thay `GRANT CONNECT ON DATABASE etl_source` bằng tên thực, ví dụ `"Learning_Resources"`; reader không tự chạy SQL cấp quyền.
Tạo CA công khai nếu chưa có (Git Bash, đã cài thư viện):

```bash
test -f /d/etl/private/neon-ca.pem || python -c "import certifi,shutil; shutil.copyfile(certifi.where(), 'D:/etl/private/neon-ca.pem')"
```

Bỏ/comment `SOURCE_MIGRATION_DATABASE_URL`. `SOURCE_POSTGRES_*` có thể giữ cho container nguồn local cũ;
container còn xuất hiện không có nghĩa ETL đọc nó. Không xóa volume cũ.
Restart API/cả ba worker. Đổi URL không chuyển schema/dữ liệu hoặc đặt lại cursor; khi đổi sang nguồn có nội dung khác,
kiểm tra ID/checkpoint và kế hoạch đồng bộ trước khi tiếp tục.

## 2. Chạy ứng dụng bằng Docker

Host không cần Python/Node/Tesseract/thư viện embedding. Backend image đã cài Python3.12,
Tesseract `vie+eng`, LibreOffice và embedding dependencies; ba worker dùng chung image Backend.
**Model không nằm trong image**, được chuẩn bị một lần rồi mount read-only. Build cần Internet nếu chưa có image/dependency cache.

### 2.1. Tạo `docker.env` và JSON kết nối

```bash
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/models/bge-m3
test -f docker.env || cp deploy/docker.env.example docker.env
test -f /d/etl/private/connections.json || cp deploy/config/connections.example.json /d/etl/private/connections.json
notepad.exe docker.env
```

Sửa các key đã có, không thêm dòng trùng:

```dotenv
APP_PROJECT=etl-app
BACKEND_IMAGE=etl-portable-backend:local
FRONTEND_IMAGE=etl-portable-frontend:local
ETL_DATA_NETWORK=etl-data
WEB_PORT=8080

MODEL_DIR=D:/etl/models/bge-m3
MODEL_MANIFEST_FILE=D:/etl/models/bge-m3/etl-model-manifest.json
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[]
SEARCH_MODEL_WARMUP_TIMEOUT_SECONDS=120
INDEX_AUTO_ENABLED=false
MASTER_SYNC_AUTO_INTERVAL_SECONDS=30

CONNECTIONS_FILE=D:/etl/private/connections.json
DATABASE_MODE=same-host
SOURCE_LOCAL_DIR=D:/etl/source
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true

AUTH_MODE=single_admin
AUTH_APP_ENVIRONMENT=production
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:8080
ADMIN_LOGIN_EMAIL=YOUR_ADMIN_EMAIL
ADMIN_PASSWORD_HASH_SECRET_FILE=D:/etl/private/auth/admin_password_hash
SESSION_SIGNING_KEY_SECRET_FILE=D:/etl/private/auth/session_signing_key
AUTH_PROXY_KEY_SECRET_FILE=D:/etl/private/auth/auth_proxy_key
```

BGE-M3 nạp riêng ở API/OCR Worker; cấp RAM Docker phù hợp, tăng `API_MEMORY_LIMIT`/`OCR_MEMORY_LIMIT` nếu log báo thiếu bộ nhớ.
`MODEL_DIR`/`MODEL_MANIFEST_FILE` là đường dẫn tuyệt đối **host**; container mount tại đường dẫn nội bộ cố định.
Mở `D:/etl/private/connections.json`; ví dụ dùng DB local đã tạo ở mục1.5:

```json
{
  "DATABASE_URL": "postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@postgres:5432/etl",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@source-postgres:5432/etl_source",
  "MONGO_URI": "mongodb://mongo:27017",
  "MONGO_DATABASE": "etl_content"
}
```

Ví dụ Mongo trên chỉ dành cho stack DB local cô lập chưa bật auth. DB đã có auth dùng runtime account/`authSource` đúng,
như [mẫu kết nối](deploy/config/connections.example.json); không tắt auth DB hiện có để khớp ví dụ.
JSON chỉ có **đúng4key** trên, không thêm `SOURCE_MIGRATION_DATABASE_URL`.
`127.0.0.1` trong container là chính container đó; DB cùng network dùng service/alias `postgres`, `source-postgres`, `mongo`.

### 2.2. Database và network

Ứng dụng/DB là hai stack riêng. DB có sẵn không chạy migration. Cài mới hoàn toàn:
chuẩn bị nhóm DB trong `.env` theo mục1.4, đặt dimension1024 rồi chạy bước khởi tạo ở mục1.5.
Bước đó dùng Docker, không cần Python host; `.env` lúc này chỉ phục vụ Compose DB ở gốc.

Tạo network rồi nối container DB có sẵn (Git Bash):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Network

docker network connect --alias postgres etl-data etl-master-data-postgres-1
docker network connect --alias source-postgres etl-data etl-master-data-source-postgres-1
docker network connect --alias mongo etl-data etl-master-data-mongo-1
```

Đổi tên container theo `docker ps -a` nếu khác. Endpoint already exists nghĩa là đã nối, không xóa network.
SOURCE dùng Neon thì bỏ lệnh nối `source-postgres`; MASTER/Mongo local vẫn nối.
DB đã tồn tại nhưng dừng: `docker compose up -d postgres mongo`, thêm `source-postgres` nếu còn dùng nguồn local.

Có thể chọn [deploy/compose.database.yml](deploy/compose.database.yml) thay stack DB gốc:
stack này dùng secret, không publish port, **không migration/bootstrap**. DBA provision schema, roles và Mongo validators trước.
MASTER service tên `master-postgres`, SOURCE tên `source-postgres`; sửa JSON tương ứng.
Không nối hai stack Mongo cùng alias `mongo` vào `etl-data`. `DbStart` không tự sửa schema thiếu.

### 2.3. Build, model và secret

Trong **Git Bash**, xuống dòng bằng `\`, không dùng backtick PowerShell:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/etl-docker.ps1 -Action Build
```

Build API/Frontend; ba worker dùng chung image Backend. Build chưa khởi động ứng dụng.
Chưa có model thì tải bằng image vừa build, không cần Python host:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/etl-docker.ps1 -Action PrepareModel \
  -ModelDirectory D:/etl/models/bge-m3 \
  -ModelId BAAI/bge-m3 \
  -ModelRevision 5617a9f61b028005a4858fdac845db406aefb181 \
  -ModelDimension 1024 -AcceptModelLicense

powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/etl-docker.ps1 -Action VerifyModel
```

`PrepareModel` dùng `etl-portable-backend:local` mặc định; đổi tag thì đặt `ETL_MODEL_TOOL_IMAGE` cho đúng.
Có model/manifest rồi chỉ VerifyModel. Không tải đồng thời vào cùng thư mục.
Tạo auth secret nếu chưa có, thay email bằng email admin thật:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File deploy/scripts/setup-auth.ps1 \
  -Directory D:/etl/private/auth -AdminEmail YOUR_ADMIN_EMAIL \
  -Image etl-portable-backend:local
```

Nhập password ít nhất15ký tự. Script **không tự sửa `docker.env`**; kiểm tra email/ba secret path khớp.
Có secret từ local thì dùng lại, không init lại.
Dùng cả user MASTER: đổi `AUTH_MODE=master_users`, thêm `AUTH_MASTER_FRESHNESS_SECONDS=120`,
`AUTH_MASTER_PBKDF2_SALT_ENCODING=auto` trong `docker.env`; Full baseline/Auto users giống mục1.7.
Giữ launcher profile mặc định `auth`; `db-auth` chỉ dành cho Auth DB riêng đã provision.

### 2.4. Check, Start, Stop và cập nhật code

Check kết nối DB để kiểm tra chỉ đọc; Start chạy Check rồi khởi động ứng dụng/worker có thể ghi MASTER/Mongo và đọc nguồn.
Chỉ chạy trên môi trường được phép vận hành:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -Workers
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Logs
```

Mở **http://127.0.0.1:8080/login**, Swagger admin **http://127.0.0.1:8080/docs**.
API không cần publish cổng riêng; Nginx proxy `/api`, `/docs`, `/openapi.json`.
Bỏ `-Workers` thì Start chỉ tạo mới API/Frontend; build không tự chạy worker.

| Thay đổi | Lệnh cần chạy |
| --- | --- |
| Source code Backend/Frontend | Build → Start -Workers |
| Chỉ docker.env | Start -Workers để Compose tạo lại container khi cấu hình đổi |
| Nội dung JSON/secret đổi, đường dẫn giữ nguyên | Stop → Check → Start -Workers để mọi tiến trình đọc lại secret |
| Model/revision/dimension | Prepare/VerifyModel, kiểm tra schema/index rồi Start |
| Build nhưng giao diện cũ | Kiểm tra tag trong env đang chọn, Build đúng tag rồi Start, tải lại trình duyệt |

Dừng ứng dụng, giữ DB/volume:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Stop
```

Ưu tiên env: `-EnvFile` → `ETL_DOCKER_ENV_FILE` → `docker.env` gốc → file cũ `deploy/.env.docker`.
Script in env/profile đang dùng. Vẫn truyền `-EnvFile D:/etl/private/app.env` thì sửa file đó hoặc bỏ tham số để dùng `docker.env`.
DB dừng riêng; không dùng `down -v` để chữa lỗi. Đổi nội dung secret cùng đường dẫn không bảo đảm Compose tự tạo lại container;
cần Stop/Start để tiến trình đọc lại. Đổi mật khẩu/hash có thể yêu cầu đăng nhập lại.

### 2.5. Docker đổi DB remote / SOURCE Neon

**Chỉ SOURCE là Neon, MASTER/Mongo vẫn local:** trong `docker.env`:

```dotenv
DATABASE_MODE=source-neon
CONNECTIONS_FILE=D:/etl/private/connections.json
SOURCE_DATABASE_CA_FILE=D:/etl/private/neon-ca.pem
```

Giữ MASTER/Mongo trong JSON, thay SOURCE như sau:

```json
{
  "DATABASE_URL": "postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@postgres:5432/etl",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@ep-YOUR-ENDPOINT.REGION.aws.neon.tech:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=/run/secrets/source-database-ca.pem",
  "MONGO_URI": "mongodb://mongo:27017",
  "MONGO_DATABASE": "etl_content"
}
```

Nếu chưa có CA host, xuất trust bundle công khai từ image bằng Git Bash (thay tag nếu cần):

```bash
test -f /d/etl/private/neon-ca.pem || MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only \
  --entrypoint cat etl-portable-backend:local /etc/ssl/certs/ca-certificates.crt > /d/etl/private/neon-ca.pem
```

Launcher tự chọn `compose.source-neon.yml`, không cần sửa Compose/thêm `-SourceNeon`.
Mode này yêu cầu SOURCE `*.neon.tech`, reader, verify-full và channel binding;
MASTER là `postgres`/`master-postgres`, Mongo là `mongo`. MASTER/Mongo cũng remote thì dùng mode tiếp theo.

**DB remote với TLS:** trong `docker.env`:

```dotenv
DATABASE_MODE=remote
DATABASE_CA_FILE=D:/etl/private/database-ca.pem
CONNECTIONS_FILE=D:/etl/private/connections.json
```

Trong JSON; trust bundle phải phù hợp chứng chỉ của các DB:

```json
{
  "DATABASE_URL": "postgresql+psycopg://MASTER_USER:ENCODED_PASSWORD@master-db.example.com:5432/etl?sslmode=verify-full&sslrootcert=/run/secrets/database-ca.pem",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@source-db.example.com:5432/Learning_Resources?sslmode=verify-full&sslrootcert=/run/secrets/database-ca.pem",
  "MONGO_URI": "mongodb://MONGO_USER:ENCODED_PASSWORD@mongo-db.example.com:27017/etl_content?authSource=etl_content&tls=true&tlsCAFile=/run/secrets/database-ca.pem",
  "MONGO_DATABASE": "etl_content"
}
```

Neon trong mode này vẫn nên thêm `channel_binding=require`. Mongo Atlas dùng URI/SRV, `authSource`, CA theo nhà cung cấp và giữ TLS.
Launcher tự chọn overlay remote. Không dùng `source-neon` và `remote` đồng thời.

| Khi đổi DB | Local | Docker |
| --- | --- | --- |
| MASTER host/user/password/database | `.env`: `DATABASE_URL` | JSON: `DATABASE_URL` |
| SOURCE host/user/password/database | `.env`: `SOURCE_DATABASE_URL` | JSON: `SOURCE_DATABASE_URL` |
| Mongo URI/database | `.env`: `MONGO_URI`, `MONGO_DATABASE` | Cùng key trong JSON |
| CA | URL trỏ file host | Env trỏ CA host; URL trỏ `/run/secrets/...` tương ứng |
| Chọn topology | URL trong `.env` | `DATABASE_MODE=same-host`, `source-neon` hoặc `remote` |

DB phải có schema/roles trước; firewall cho container đến đúng cổng. `etl-data` vẫn cần tồn tại kể cả DB remote.
Đổi DB không chuyển dữ liệu hoặc reset cursor; kiểm tra checkpoint đồng bộ trước Start, không tự migration remote.
Sau khi đổi JSON/CA: chạy Stop → Check → Start -Workers, để cả API và worker đọc URL/CA mới.
Remote DB không có nghĩa mở giao diện LAN: ví dụ vẫn localhost. LAN cần HTTPS và auth TLS profile theo [Docker guide](deploy/README.md).

### 2.6. Linux và chuyển sang máy khác

Linux dùng cùng `docker.env`, thay mọi đường dẫn Windows bằng đường dẫn tuyệt đối trên máy:

```bash
bash deploy/scripts/etl-docker.sh build
bash deploy/scripts/etl-docker.sh network
# Chỉ sau khi DB/network/model/secret đã chuẩn bị:
bash deploy/scripts/etl-docker.sh check
bash deploy/scripts/etl-docker.sh start --workers
bash deploy/scripts/etl-docker.sh status
bash deploy/scripts/etl-docker.sh stop
```

Tạo auth secret mới trên Linux, không cần Python host:

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /srv/etl-private/auth
sudo env ETL_AUTH_SETUP_IMAGE=etl-portable-backend:local bash deploy/scripts/setup-auth.sh /srv/etl-private/auth YOUR_ADMIN_EMAIL
```

Lệnh setup dùng sudo để đọc thư mục riêng0700 và tạo secret bằng container UID10001.
Connections/CA cần UID10001 đọc được; secret auth owner10001/mode0600, proxy key group10001/mode0640 để Nginx đọc.
Model/source cần quyền đọc/traverse; không chmod777. Rootless Docker cần kiểm tra UID mapping riêng.
Chuẩn bị model trên Linux bằng container (hoặc chép nguyên thư mục đã verify từ máy khác):

```bash
ETL_MODEL_TOOL_IMAGE=etl-portable-backend:local bash deploy/scripts/etl-docker.sh prepare-model \
  "$HOME/etl-models/bge-m3" --accept-model-license \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
```

Đặt `MODEL_DIR`/`MODEL_MANIFEST_FILE` trong `docker.env` theo đường dẫn tuyệt đối vừa tạo rồi `verify-model`.

Xuất/nhập image trong Git Bash Windows; tar nằm ở **đường dẫn bạn tự chọn**:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Export -Archive D:/etl/etl-images.tar
# Máy nhận đã có repo và docker.env hợp lệ:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Import -Archive D:/etl/etl-images.tar
```

Chuyển cả tar/sha256. Image chứa OCR/thư viện, máy nhận không tải lại chúng; model/manifest, nguồn file và credentials cấp riêng.
Sửa path host trong `docker.env`; image tar không chứa DB/volume. Offline hoàn toàn còn cần image DB nếu tự chạy DB.

## 3. Nguồn file và quyền

URL SOURCE DB chỉ cung cấp metadata/tham chiếu, không tự cấp quyền đọc file.
Local nguồn phải có đúng bucket/object_key; Docker mount `SOURCE_LOCAL_DIR` read-only tại `/mnt/source`.
File cần trạng thái nguồn/SHA-256 hợp lệ. Hai cờ reference/temp không cho phép lưu bản sao SOURCE lâu dài.

Google Drive tùy chọn: [hướng dẫn Drive](deploy/DRIVE_SETUP.md). Local cài extra `google-drive`, đặt:

```dotenv
SOURCE_DRIVE_ENABLED=true
SOURCE_DRIVE_BUCKETS={"DRIVE":"YOUR_APPROVED_FOLDER_ID"}
SOURCE_DRIVE_AUTH_TYPE=service_account
SOURCE_DRIVE_CREDENTIALS_FILE=D:/etl/private/drive-reader.json
```

Docker dùng `DRIVE_CREDENTIALS_FILE` (path host), `SOURCE_DRIVE_BUCKETS`, `SOURCE_DRIVE_AUTH_TYPE` trong `docker.env`,
thêm **`-Drive`** cho Build/Check/Start/Status/Logs/Stop; Bash dùng `ETL_WITH_DRIVE=true`.
Credential cần quyền Reader tại nguồn; không có token dùng chung. Khi tắt Drive giữ bucket mapping để tránh định tuyến nhầm sang local.

Kho cá nhân theo `created_by` MASTER; public chỉ tìm/xem/tải khi đủ điều kiện nội dung/quyền. Không chỉnh env theo từng tệp.
Chủ học liệu/ADMIN sửa mô tả, tác giả, thẻ trong Mongo qua ID liên kết; title/provider/type đồng bộ chỉ đọc.
Hồ sơ cá nhân cho sửa cơ quan, địa chỉ, giới thiệu trong Mongo; tên/email/role đồng bộ giữ chỉ đọc.

## 4. Kiểm tra và xử lý lỗi

### 4.1. Các lỗi thường gặp

| Lỗi/hiện tượng | Cách kiểm tra/sửa |
| --- | --- |
| Thiếu `argon2` | Kích hoạt đúng venv, `python -m pip install -e ".[dev,embeddings,google-drive]"`; dùng chính Python đó chạy API. |
| Launcher trỏ repo cũ | Tạo lại venv tại đường dẫn mới; dùng `python -m ...` thay executable cũ. |
| `AUTH_MASTER_STALE` | Master Sync Worker, Full baseline, Auto users bật; users COMPLETED trong120s. Không bỏ guard hoặc tăng freshness để che lỗi. |
| Sai đăng nhập | Phân biệt password user/admin/DB; kiểm tra mode, secret, account/role/email verified và sync. |
| `/docs` trắng, schema403 | Login admin rồi mở3000/docs local hoặc8080/docs Docker; không trực tiếp8000/docs trong auth mode. |
| Thiếu network `etl-data` | Launcher Network rồi nối đúng container DB/alias. |
| `CONNECTIONS_SECRET_VALUES_INVALID` | JSON đúng4key, không placeholder, URL thật; xem đúng `CONNECTIONS_FILE`. |
| `SOURCE_READ_ONLY_ROLE_REQUIRED` / `SOURCE_WRITE_PRIVILEGES_FORBIDDEN` | DBA kiểm tra reader; không đổi sang owner để vượt preflight. |
| `DATABASE_SCHEMA_NOT_READY` | Kiểm tra đúng database/schema đã provision; không tự migration remote. |
| `BLOCKED_INDEX_SCHEMA` | Schema --check, dimension/history khớp validator; thuần1024 dùng `[]`. |
| Index FAILED sau đổi env | Restart OCR Worker, xem error_code của index-status rồi Tái lập. Worker cũ giữ env trong bộ nhớ. |
| `SOURCE_REFERENCE_DISABLED` / `SOURCE_REFERENCE_POLICY_BLOCKED` | Kiểm tra hai cờ reference/temp và quyền file nguồn ở đúng môi trường. |
| Word/PPT không xem PDF | Kiểm tra LibreOffice/executable local hoặc rebuild image mới; tải xuống vẫn file gốc. |
| Search rỗng | CONTENT_READY, index READY, đúng chủ sở hữu hoặc điều kiện public; chỉ model READY chưa đủ. |
| Đổi Docker env nhưng vẫn cũ | Xem file env script in ra, JSON secret đúng, Start lại cả worker; sửa code thì Build trước. |

### 4.2. Đã có dữ liệu MiniLM384, muốn sang BGE-M3

Không dùng vector384 như vector1024. Dừng API/cả ba worker, giữ auto index tắt, backup và duyệt kế hoạch trước.
Công cụ sau **chỉ hỗ trợ MASTER/Mongo local**, cần schema ban đầu384 hợp lệ:

```bash
python scripts/transition_embedding_model.py
# Chỉ sau khi xem kế hoạch, được phép maintenance; thư mục backup phải mới:
python scripts/transition_embedding_model.py --apply --confirm-local-maintenance \
  --backup-directory D:/etl/private/model-transition-backup-NEW
```

Công cụ bảo toàn bag384 lịch sử, chuyển validator và xây lại vector. Sau chuyển dùng
`INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[384]` theo validator đã chuyển, cho cả local/Docker.
DB thuần1024 hoặc đã chuyển rồi không chạy lại công cụ; thuần1024 giữ `[]`.
Chỉ Start lại khi báo cáo COMPLETE/schema hợp lệ và không còn run FAILED; rollback cần backup schema/bag/index đồng bộ,
không chỉ đổi đường dẫn model về MiniLM.

### 4.3. Tự kiểm tra

Local đã cài extra dev; các bài sau dùng fixture cô lập:

```bash
python -m pytest tests/unit/test_schema_diagnostics.py tests/unit/test_resource_description.py -q
python -m pip install PyYAML
python -m pytest deploy/tests -q
npm.cmd --prefix frontend run build
node --experimental-strip-types --test frontend/tests/fileReadiness.test.mjs frontend/tests/sourceReference.test.mjs
```

Nếu `frontend/dist` đang bị khóa, build kiểm chứng vào thư mục mới:
`npm.cmd --prefix frontend run build -- --outDir ../.test-tmp/frontend-verify`.
Integration test chỉ dùng DB test riêng, không Dev/Production. Check Docker đọc DB thật đã cấu hình;
Start/worker ghi MASTER/Mongo và đọc nguồn, không coi đó là unit test.

Tài liệu bổ sung: [Docker](deploy/README.md), [MASTER users](deploy/AUTH_MASTER_USERS_SETUP.md),
[Auth DB riêng](deploy/AUTH_DATABASE_SETUP.md), [Google Drive](deploy/DRIVE_SETUP.md).

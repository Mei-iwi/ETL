# ETL — Kho học liệu số

Đồng bộ SOURCE → MASTER, trích xuất PDF/Office/ảnh/MP3/MP4 và tìm kiếm BoW, BGE-M3 Vector, Hybrid. Backend FastAPI, frontend React/TypeScript, PostgreSQL và MongoDB. Tệp **SOURCE chỉ lưu tham chiếu**, đọc tạm từ nguồn khi xử lý/xem/tải; tệp **LOCAL** dùng storage riêng.

Hướng dẫn theo thứ tự **local trước, Docker sau**. Cài mới nên dùng `single_admin` và BGE-M3 1024 chiều. Sau khi đồng bộ users ổn định mới chuyển sang `master_users`. Không có mật khẩu đăng nhập mặc định.

## 1. Quy ước và vị trí cấu hình

Khối `bash` chạy bằng **Git Bash trên Windows** hoặc Bash trên Linux, tại gốc repo. Xuống dòng dùng `\`, không dùng backtick PowerShell. Khối `powershell` chạy trong PowerShell. Linux dùng `npm` thay `npm.cmd`.

```bash
git clone https://github.com/Mei-iwi/ETL.git
cd ETL
# Đã có repo: mở terminal tại thư mục chứa pyproject.toml, không clone lại.
```

| Vị trí | Vai trò |
| --- | --- |
| `.env.example` → `.env` ở gốc | API/worker local và DB Compose ở gốc repo |
| `deploy/docker.env.example` → `docker.env` ở gốc | Docker app: image, mount, auth, feature flags và profile DB |
| `D:/etl/private/connections.json` | Đúng 4 thông số DB runtime Docker; không lấy từ `.env` |
| `D:/etl/private/ops-connections.json` | Kết nối owner, chỉ dùng khi chủ DB khởi tạo/bảo trì |
| `D:/etl/private/auth/` | Password hash, session signing key và proxy key |
| `D:/etl/models/bge-m3/` | BGE-M3, revision marker, manifest checksum |
| `D:/etl/models/faster-whisper-large-v3/` | Model nhận dạng tiếng nói, nếu bật media |
| `D:/etl/models/argos/` | Gói dịch offline, nếu cần dịch |
| `D:/etl/source/`, `source-temp/`, `storage/` | Ba thư mục riêng: nguồn, tệp tạm SOURCE, storage LOCAL |

`D:/etl` là đường dẫn gợi ý Windows. Linux dùng thư mục riêng, ví dụ `/home/<user>/etl-runtime`; thay mọi đường dẫn cấu hình bằng **đường dẫn tuyệt đối thật**, không giữ `<user>`/`YOUR_*`. Không commit env thật, secret, model, dữ liệu. Biến export trong terminal có thể ghi đè env file.

## 2. Chạy local

### 2.1. Cài công cụ hệ thống

Cần Python **3.12**, Node.js **22.12+** (khuyến nghị 24), Git. Docker chỉ cần nếu chạy DB bằng container. Dành đủ dung lượng cho model/dependencies; chạy đồng thời BGE-M3 và Whisper large-v3 nên dùng máy RAM khoảng 32 GB. Máy ít RAM cần giảm tải/chọn Whisper nhỏ hơn và tự kiểm chứng chất lượng, thời gian.

**Windows — PowerShell:**

```powershell
winget install --id Python.Python.3.12 --exact
winget install --id OpenJS.NodeJS.LTS --exact
winget install --id Git.Git --exact
winget install --id UB-Mannheim.TesseractOCR --exact
winget install --id TheDocumentFoundation.LibreOffice --exact
winget install --id Gyan.FFmpeg --exact
```

Mở lại terminal sau khi cài. Tesseract dùng OCR; LibreOffice dựng bản xem Word/PPT; cần **cả FFmpeg và ffprobe** để xử lý media. Nếu thiếu ngôn ngữ OCR, chạy PowerShell Administrator:

```powershell
foreach ($lang in @('vie','eng')) {
    $file = "C:\Program Files\Tesseract-OCR\tessdata\$lang.traineddata"
    if (-not (Test-Path -LiteralPath $file)) {
        Invoke-WebRequest "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$lang.traineddata" -OutFile $file
    }
}
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
ffmpeg -version
ffprobe -version
node --version
py -3.12 --version
```

**Ubuntu 24.04 — Bash:**

```bash
sudo apt-get update
sudo apt-get install -y git curl python3.12 python3.12-venv tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng libreoffice-writer libreoffice-impress ffmpeg
# Nếu chưa có Node phù hợp, cài bằng nvm:
curl -fL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh -o /tmp/etl-install-nvm.sh
bash /tmp/etl-install-nvm.sh
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm install 24
nvm use 24
node --version
tesseract --list-langs
ffmpeg -version
ffprobe -version
```

### 2.2. Tạo môi trường dự án

**Windows Git Bash:**

```bash
py -3.12 -m venv .venv
source .venv/Scripts/activate
export ETL_HOME=D:/etl
```

**Linux:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
export ETL_HOME="$HOME/etl-runtime"
```

**Cả hai OS** (Linux đổi `npm.cmd` thành `npm`):

```bash
python -m pip install --upgrade pip
python -m pip install 'torch==2.14.1+cpu' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[dev,embeddings,google-drive,media,translation]'
python -m pip check
npm.cmd --prefix frontend ci
test -f .env || cp .env.example .env
mkdir -p "$ETL_HOME/private/auth" "$ETL_HOME/models" "$ETL_HOME/source" "$ETL_HOME/source-temp" "$ETL_HOME/storage"
```

Không dùng media có thể cài `.[dev,embeddings,google-drive]`. Extra media dùng faster-whisper 1.2.1/PyAV `<17`; không tự nâng PyAV riêng. Không chép venv từ máy/đường dẫn khác. Nếu `hf.exe`/`pip.exe` báo launcher ở đường dẫn cũ, tạo venv mới, dùng `python -m pip`; các lệnh tải dưới đây không cần `hf.exe`.

### 2.3. Tải và kiểm tra model

Cần Internet lúc tải và chấp nhận giấy phép model. Đã có model đúng revision/manifest thì dùng lại. Runtime đọc local, không tải lại khi start.

**BGE-M3 1024 chiều**, theo [revision chính thức](https://huggingface.co/BAAI/bge-m3/tree/5617a9f61b028005a4858fdac845db406aefb181):

```bash
mkdir -p "$ETL_HOME/models/bge-m3"
python deploy/runtime/model.py prepare --directory "$ETL_HOME/models/bge-m3" --manifest "$ETL_HOME/models/bge-m3/etl-model-manifest.json" --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024 --accept-model-license
python deploy/runtime/model.py verify --directory "$ETL_HOME/models/bge-m3" --manifest "$ETL_HOME/models/bge-m3/etl-model-manifest.json" --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
python deploy/runtime/model.py encode --directory "$ETL_HOME/models/bge-m3" --manifest "$ETL_HOME/models/bge-m3/etl-model-manifest.json" --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
```

`prepare` tải và tạo manifest; `verify` kiểm tra revision/checksum; `encode` nạp model thật và kiểm tra 1024 chiều. Cần có weights, tokenizer, `modules.json`, `1_Pooling/`, `etl-model-revision.txt`, `etl-model-manifest.json`. Thư mục có tên `bge-m3` chưa chứng minh model đúng.

**Whisper cho MP3/MP4**, bỏ qua nếu không dùng media. Dùng [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3/tree/edaa852ec7e145841d8ffdb056a99866b5f0a478), định dạng CTranslate2:

```bash
HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 HF_HUB_DISABLE_XET=1 python - "$ETL_HOME/models/faster-whisper-large-v3" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download(repo_id="Systran/faster-whisper-large-v3",
    revision="edaa852ec7e145841d8ffdb056a99866b5f0a478", local_dir=sys.argv[1],
    allow_patterns=["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"])
PY
python - "$ETL_HOME/models/faster-whisper-large-v3" <<'PY'
import hashlib, sys
from pathlib import Path
p = Path(sys.argv[1])
for name in ("config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"):
    assert (p/name).is_file() and (p/name).stat().st_size > 0, name
with (p/"model.bin").open("rb") as f:
    assert hashlib.file_digest(f,"sha256").hexdigest() == "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
print("Whisper files/checksum OK")
PY
```

Không thay bằng model `.en`, turbo/distil hoặc weights PyTorch. Muốn dùng model nhỏ hơn phải tải đúng repo/revision và đổi đường dẫn, không chỉ đổi tên thư mục.

**Argos Anh → Việt, tùy chọn.** Chỉ cần nguyên ngữ thì đặt `TRANSLATION_PROVIDER=disabled`, bỏ bước này. Gói trong [danh mục Argos chính thức](https://github.com/argosopentech/argospm-index/blob/main/index.json):

```bash
mkdir -p "$ETL_HOME/models/argos"
curl -fL --retry 3 -A 'Mozilla/5.0' https://argos-net.com/v1/translate-en_vi-1_9.argosmodel -o "$ETL_HOME/models/translate-en_vi-1_9.argosmodel"
python - "$ETL_HOME/models/translate-en_vi-1_9.argosmodel" "$ETL_HOME/models/argos" <<'PY'
import sys
from zipfile import ZipFile
with ZipFile(sys.argv[1]) as package:
    package.extractall(sys.argv[2])
PY
```

Cần `argos/translate-en_vi-1_9/metadata.json`, tokenizer và `model/model.bin`. `TRANSLATION_MODEL_ROOT` trỏ **thư mục cha `argos`**. Ngôn ngữ khác cần gói tương ứng; gói en→vi không dịch mọi ngôn ngữ. Thiếu model dịch không được coi là có bản dịch.

### 2.4. Cấu hình `.env`

Sửa các khóa đã có, tránh khai báo lặp. Mẫu Windows cho DB local; Linux thay mọi `D:/etl` bằng đường dẫn thực và dùng `/usr/bin/tesseract`, `/usr/bin/libreoffice`:

```dotenv
APP_ENVIRONMENT=development
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
SOURCE_MIGRATION_DATABASE_URL=postgresql+psycopg://etl_source_admin:YOUR_SOURCE_OWNER_PASSWORD@127.0.0.1:5433/etl_source
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
FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
INDEX_EMBEDDING_MODEL_PATH=D:/etl/models/bge-m3
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[]
INDEX_AUTO_ENABLED=false
MASTER_SYNC_AUTO_INTERVAL_SECONDS=30
SEARCH_MODEL_WARMUP_TIMEOUT_SECONDS=120
SOURCE_METADATA_MEDIA_ENABLED=false
WHISPER_MODEL_PATH=D:/etl/models/faster-whisper-large-v3
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
TRANSLATION_PROVIDER=disabled
# Đã cài Argos: đổi provider thành argos và bỏ comment dòng root.
# TRANSLATION_MODEL_ROOT=D:/etl/models/argos
TRANSLATION_TARGET_LANGUAGE=vi
```

Thay password thật, percent-encode ký tự đặc biệt trong URL PostgreSQL/MongoDB. Với Compose ở gốc, nên dùng password owner chỉ gồm chữ/số để các URL migration nội suy không bị sai; có thể tự tạo bằng `python -c "import secrets; print(secrets.token_hex(24))"`, dùng hai giá trị khác nhau cho hai owner. `SOURCE_MIGRATION_DATABASE_URL` chỉ dùng owner của DB nguồn riêng mới/trống; bỏ dòng này khi dùng SOURCE có sẵn/remote. `SOURCE_ADAPTER=json` chỉ đọc fixture, không đọc PostgreSQL.

`SOURCE_METADATA_MEDIA_ENABLED=false`: xử lý nội dung MP3/MP4; `true`: chỉ đăng ký metadata. Tệp local-source nằm ở `<SOURCE_FILE_STORAGE_ROOT>/<bucket>/<object_key>` theo metadata nguồn; SHA-256 phải khớp. Ba thư mục nguồn/storage/tạm phải tách riêng, không lồng nhau. Nếu FFmpeg/ffprobe chưa vào PATH, đặt đường dẫn binary tuyệt đối rồi restart tiến trình.

### 2.5. Chuẩn bị database

**DB có sẵn:** chỉ sửa kết nối và kiểm tra. Không bootstrap/seed lại hoặc tự đổi validator384→1024 trên dữ liệu cũ:

```bash
python -m alembic current
python -m data_sync_etl.adapters.mongo.schema --check
```

Nâng DB có dữ liệu cần chủ DB sao lưu, kiểm tra lịch sử migration và dừng API/worker trước khi phê duyệt. MASTER cần revision `0019` cho lịch/queue đối soát (`0018` là revision media); dùng `head` theo checkout, không tự `stamp` để bỏ qua lỗi.

**Chỉ cho ba DB local mới/trống**, dùng Compose ở gốc repo:

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
# Cả 3 job phải exit0; mongo-init dùng INDEX_EMBEDDING_DIMENSION=1024 trong .env.
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong psql chạy `\password etl_source_reader`, nhập password khớp `SOURCE_DATABASE_URL`, rồi `\q`. Owner SOURCE khác reader; reader chỉ SELECT các bảng đồng bộ. Dùng đúng URL MASTER cho migration MASTER, không nhầm với SOURCE.

Nếu tự cài PostgreSQL/Mongo thay vì Compose: chủ DB tạo DB/role, sau đó khởi tạo **DB rỗng**:

```bash
python -m alembic upgrade head
python -m alembic -c alembic_source.ini upgrade head
python -m data_sync_etl.adapters.mongo.schema --bootstrap-empty
```

Lệnh thứ hai cần SOURCE owner URL, không chạy trên SOURCE có sẵn. Cấp reader theo script ở trên rồi kiểm tra lại. Mongo có xác thực cần đúng `authSource` và role trong URI.

**Danh mục/dữ liệu thử, tùy chọn:** DB mới chưa có provider/loại/người dùng/học liệu. Chỉ trên SOURCE demo rỗng của bạn:

```bash
python scripts/source_seed.py --confirm-empty-demo-source
python -m data_sync_etl.cli sync-master --full --stream all
```

Seed không tự chạy và từ chối ghi đè metadata có sẵn. Không có password user demo bảo đảm đăng nhập; dùng admin riêng. Với SOURCE thật, dùng dữ liệu do hệ thống nguồn quản lý, không seed demo. DDL tham chiếu: [alembic_source/schema_v1.sql](alembic_source/schema_v1.sql).

### 2.6. Secret và tài khoản đăng nhập

Tạo một lần; nhập password ít nhất 15 ký tự ở prompt ẩn. Có secret thì dùng lại, không chạy `init` đè:

```bash
python -m data_sync_etl.security.cli init --directory "$ETL_HOME/private/auth"
```

Sửa `.env`, điền email thật:

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

`ADMIN_ENABLED=true` bật chức năng, **không bỏ login**. Không có password gốc trong file hash. Vite đọc proxy key từ `.env` backend; không cần `frontend/.env` cho cấu hình mặc định, không đặt secret vào `VITE_*`.

Đổi password: tạo hash ở tệp mới, đổi đường dẫn hash trong cấu hình và restart API; không cần đổi proxy/session key chỉ vì đổi password:

```bash
python -m data_sync_etl.security.cli hash --output "$ETL_HOME/private/auth/admin_password_hash_new"
```

### 2.7. Khởi động đầy đủ

Mỗi dòng là **một terminal riêng** tại gốc repo. Mỗi terminal Python kích hoạt cùng venv (`source .venv/Scripts/activate`, Linux `.venv/bin/activate`). API không tự chạy các worker.

| Tiến trình | Lệnh |
| --- | --- |
| API | `python -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| React/Vite | `npm.cmd --prefix frontend run dev` |
| Đồng bộ metadata | `python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1` |
| Xác minh nguồn, nhập tham chiếu, tạo job | `python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1` |
| Trích xuất/OCR/ASR và hoàn thiện chỉ mục | `python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1` |
| Đối soát theo lịch đã lưu, đồng bộ đúng ID lệch | `python -m data_sync_etl.workers.reconciliation` |

**Lệnh chạy liên tục cho cả 4 worker** — mỗi lệnh mở trong một terminal riêng, đã kích hoạt venv và đứng tại gốc repo:

```bash
# Terminal worker 1: đồng bộ metadata theo lịch Auto Sync, độc lập với đối soát.
python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1
```

```bash
# Terminal worker 2: phát hiện phiên bản nguồn, xác minh tệp và tạo job xử lý.
python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1
```

```bash
# Terminal worker 3: OCR, Office, ASR MP3/MP4 và hoàn thiện chỉ mục.
python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1
```

```bash
# Terminal worker 4: lịch đối soát từng luồng lưu trên server và queue audit.
python -m data_sync_etl.workers.reconciliation
```

Để kiểm tra một lượt rồi thoát, dùng `--once`. Các lệnh này vẫn thực hiện công việc thật theo cấu hình, không phải dry-run:

```bash
python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-check --once
python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-check --once
python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-check --once
python -m data_sync_etl.workers.reconciliation --once
```

Worker đối soát không nhận `--worker-id`; ba worker CLI cần ID riêng nếu chạy nhiều tiến trình. Muốn xem tham số mà chưa kết nối DB, thêm `--help` vào lệnh tương ứng. Worker chạy không có nghĩa lịch đã bật: chọn luồng/chu kỳ và **Lưu** trên giao diện. Chạy `--once` có thể trả IDLE nếu chưa đến lịch hoặc queue đang rỗng. Dừng worker local bằng Ctrl+C; khởi động lại sau khi đổi code/env.

```bash
curl -fsS http://127.0.0.1:8000/health
```

Mở **http://127.0.0.1:3000/login**. Swagger sau đăng nhập qua **http://127.0.0.1:3000/docs**; gọi trực tiếp backend8000 có thể bị chặn bởi boundary.

1. Đăng nhập admin → **Đồng bộ dữ liệu nguồn** → chạy Full baseline nếu chưa chạy CLI.
2. Bật Auto Incremental cho các luồng cần thiết; lịch/enable lưu trong DB, worker chạy không tự bật lịch.
3. Model/schema đã READY thì đặt `INDEX_AUTO_ENABLED=true`, restart API và worker.
4. Tệp mới: source-file worker xác minh → tạo job → OCR worker xử lý → ContentUnit/ContentBag/chỉ mục. Thêm/cập nhật tệp LOCAL từ giao diện cũng tạo job.
5. MP3/MP4 cũ ở `METADATA_ONLY` được quét lại theo lô khi source-file worker khởi động trong chế độ xử lý, xếp hàng trên đúng phiên bản cũ. Tệp bị xóa/từ chối/sai hash không tự bỏ chặn.

Dừng bằng Ctrl+C. Đổi `.env` phải restart API và **các worker liên quan**; đổi auth origin/proxy key cần restart Vite. Có thể thêm `--reload` cho API khi phát triển; tránh chạy nhiều tiến trình model trùng.

### 2.8. User MASTER cùng admin riêng

Sau Full users thành công, bật Auto users và giữ Master Sync Worker chạy, sửa `.env`:

```dotenv
AUTH_MODE=master_users
AUTH_MASTER_FRESHNESS_SECONDS=120
AUTH_MASTER_PBKDF2_SALT_ENCODING=auto
MASTER_SYNC_AUTO_INTERVAL_SECONDS=30
ADMIN_ENABLED=true
```

Restart API/Vite. Admin riêng dùng email/hash cấu hình; user MASTER dùng password tài khoản nguồn đã đồng bộ, không dùng password PostgreSQL. Tài khoản phải active, email verified, có role hợp lệ. ADMIN lấy từ role đã xác minh, không từ dropdown/client flag.

`AUTH_MASTER_STALE`: lần đồng bộ users thành công quá hạn. Kiểm tra heartbeat/lịch/thời gian chạy, không chỉ watermark incremental và không nới freshness để che lỗi. Auth DB độc lập `AUTH_MODE=db_users` có quy trình riêng: [AUTH_DATABASE_SETUP.md](deploy/AUTH_DATABASE_SETUP.md). Cấu hình mặc định ở đây bắt login; không bật localhost bypass trên LAN.

## 3. Chạy Docker

Host cần Git, Docker Desktop **Linux containers + Compose v2** trên Windows hoặc Docker Engine/Compose v2 trên Linux. Host không cần Python/Node/Tesseract/embedding. Image có công cụ; **model, secret, DB không nằm trong image**. Có thể dùng lại model từ2.3 hoặc tải bằng container dưới đây.

Máy chưa có Docker: cài theo [Windows](https://docs.docker.com/desktop/setup/install/windows-install/) hoặc [Ubuntu](https://docs.docker.com/engine/install/ubuntu/). Windows bật virtualization trong BIOS, mở PowerShell Administrator:

```powershell
wsl --install --no-distribution
wsl --update
winget install --id Docker.DockerDesktop --exact
```

Khởi động lại Windows nếu được yêu cầu, mở Docker Desktop, chọn WSL 2 và Linux containers, chờ Engine chạy. Các lệnh dự án bên dưới chạy trong Git Bash.

Ubuntu 24.04 mới, chưa có gói Docker xung đột:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Đăng xuất rồi đăng nhập lại để áp dụng nhóm Docker (nhóm này có quyền tương đương root). Máy đã có Docker/Podman cần xử lý gói xung đột theo hướng dẫn chính thức trước khi cài. Clone repo bằng lệnh ở mục 1 rồi đứng tại thư mục gốc.

### 3.1. Tạo thư mục và `docker.env`

```bash
docker version
docker compose version
# Git Bash Windows:
export ETL_HOME=D:/etl
export MSYS_NO_PATHCONV=1
# Linux: thay dòng export ETL_HOME ở trên bằng:
# export ETL_HOME="$HOME/etl-runtime"
mkdir -p "$ETL_HOME/private" "$ETL_HOME/models" "$ETL_HOME/source"
test -f docker.env || cp deploy/docker.env.example docker.env
test -f "$ETL_HOME/private/connections.json" || cp deploy/config/connections.example.json "$ETL_HOME/private/connections.json"
test -f "$ETL_HOME/private/ops-connections.json" || cp deploy/config/ops-connections.example.json "$ETL_HOME/private/ops-connections.json"
```

Sửa `docker.env`, giữ các khóa khác từ example:

```dotenv
BACKEND_IMAGE=etl-portable-backend:local
FRONTEND_IMAGE=etl-portable-frontend:local
WEB_PORT=8080
ETL_DATA_NETWORK=etl-data
DATABASE_MODE=same-host
CONNECTIONS_FILE=D:/etl/private/connections.json
MODEL_DIR=D:/etl/models/bge-m3
MODEL_MANIFEST_FILE=D:/etl/models/bge-m3/etl-model-manifest.json
SOURCE_LOCAL_DIR=D:/etl/source
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[]
INDEX_AUTO_ENABLED=false
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
MEDIA_ENABLED=false
INSTALL_MEDIA=false
```

Đổi WEB_PORT phải đổi AUTH_ORIGIN. Đường dẫn host dùng `D:/...` hoặc `/home/...`; path container dùng `/opt/...`/`/run/secrets/...`, không trộn.

Sửa `connections.json` runtime, đúng4 key, thay password thật:

```json
{
  "DATABASE_URL": "postgresql+psycopg://etl_master_runtime:YOUR_RUNTIME_PASSWORD@master-postgres:5432/etl_master",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@source-postgres:5432/etl_source",
  "MONGO_URI": "mongodb://etl_runtime:YOUR_MONGO_RUNTIME_PASSWORD@mongo:27017/etl_content?authSource=etl_content",
  "MONGO_DATABASE": "etl_content"
}
```

Container không dùng127.0.0.1 để kết nối DB khác container/host. Docker Desktop có thể dùng `host.docker.internal` cho DB trên host; Linux cần DNS/IP hợp lệ. Không thêm owner/migration URL vào runtime JSON.

### 3.2. Network, DB và build

DB có sẵn/remote: bỏ khởi tạo DB dưới đây, xem4.1. DB/app có Compose riêng:

```bash
docker network inspect etl-data >/dev/null 2>&1 || docker network create etl-data
```

**Chỉ DB Docker mới/trống:** bằng editor tạo ba file ngoài repo, mỗi file một password: `master-password`, `source-password`, `mongo-password`. Bổ sung `docker.env`:

```dotenv
DB_PROJECT=etl-databases
MASTER_DB_NAME=etl_master
SOURCE_DB_NAME=etl_source
MASTER_DB_ADMIN=etl_master_owner
SOURCE_DB_ADMIN=etl_source_owner
MONGO_DB_ADMIN=etl_mongo_owner
MASTER_PASSWORD_FILE=D:/etl/private/master-password
SOURCE_PASSWORD_FILE=D:/etl/private/source-password
MONGO_PASSWORD_FILE=D:/etl/private/mongo-password
OPS_CONNECTIONS_FILE=D:/etl/private/ops-connections.json
```

Sửa `ops-connections.json` theo [ops example](deploy/config/ops-connections.example.json): MASTER owner `etl_master_owner`, SOURCE owner `etl_source_owner`, Mongo owner `etl_mongo_owner` với `authSource=admin`; password khớp ba file trên. File này dùng `SOURCE_MIGRATION_DATABASE_URL`, không phải URL reader.

```bash
docker compose --env-file docker.env -f deploy/compose.database.yml up -d
docker compose --env-file docker.env -f deploy/compose.database.yml ps
# Windows Git Bash:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build -EnvFile docker.env
# Linux thay dòng trên bằng:
# bash deploy/scripts/etl-docker.sh build
```

Build không cần model đã tải. Muốn media: điền `MEDIA_ENABLED=true`, `INSTALL_MEDIA=true`, `WHISPER_MODEL_DIR` theo3.5 **trước build**. Build không tự chạy worker.

DB Compose không tự migration/seed. **Chỉ chủ DB mới/trống** chạy bảo trì này. Ops đọc dimension từ `docker.env`; truyền rõ 1024 để kiểm soát Mongo bootstrap:

```bash
export ETL_MAINTENANCE_APPROVAL=APPROVED_DATABASE_MAINTENANCE
OPS=(docker compose --env-file docker.env -f deploy/compose.ops.yml --profile ops)
"${OPS[@]}" run --rm ops master-migrate
"${OPS[@]}" run --rm ops source-migrate
"${OPS[@]}" run --rm -e INDEX_EMBEDDING_DIMENSION=1024 ops mongo-bootstrap-empty
unset ETL_MAINTENANCE_APPROVAL
```

MASTER runtime role, trong DB mới:

```bash
docker compose --env-file docker.env -f deploy/compose.database.yml exec master-postgres psql -U etl_master_owner -d etl_master
```

Trong psql:

```sql
CREATE ROLE etl_master_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password etl_master_runtime
GRANT CONNECT ON DATABASE etl_master TO etl_master_runtime;
GRANT USAGE ON SCHEMA public TO etl_master_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO etl_master_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO etl_master_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE etl_master_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO etl_master_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE etl_master_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO etl_master_runtime;
\q
```

SOURCE reader, password phải khớp runtime JSON:

```bash
docker compose --env-file docker.env -f deploy/compose.database.yml exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_owner -d etl_source < scripts/sql/source_reader.sql
docker compose --env-file docker.env -f deploy/compose.database.yml exec source-postgres psql -U etl_source_owner -d etl_source
# Trong psql: \password etl_source_reader ; rồi \q
```

Mongo runtime user, nhập password owner ở prompt:

```bash
docker compose --env-file docker.env -f deploy/compose.database.yml exec mongo mongosh --username etl_mongo_owner --authenticationDatabase admin --password
```

Trong mongosh:

```javascript
use etl_content
db.createUser({user: "etl_runtime", pwd: passwordPrompt(), roles: [{role: "readWrite", db: "etl_content"}]})
exit
```

DB đã có role/user thì kiểm tra và cấp quyền phù hợp, không CREATE lại mù quáng. Linux cần cho UID/GID 10001 đọc connection/model; sau khi editor đã lưu hai connection JSON:

```bash
sudo chown 10001:10001 "$ETL_HOME/private/connections.json" "$ETL_HOME/private/ops-connections.json"
sudo chmod 600 "$ETL_HOME/private/connections.json" "$ETL_HOME/private/ops-connections.json"
```

Đây là bước Linux; không chmod777 toàn bộ private. Không cấp owner secret cho API/worker.

### 3.3. Model bằng container, không cần Python host

Có model/manifest đúng từ2.3 thì bỏ tải. Các lệnh dùng image vừa build, mount thư mục ngoài image; root chỉ dùng trong container tải model một lần, không đổi user dịch vụ app:

```bash
mkdir -p "$ETL_HOME/models/bge-m3"
docker run --rm --user 0:0 --mount "type=bind,source=$ETL_HOME/models,target=/models" --entrypoint python etl-portable-backend:local /opt/etl/model.py prepare --directory /models/bge-m3 --manifest /models/bge-m3/etl-model-manifest.json --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024 --accept-model-license
docker run --rm --mount "type=bind,source=$ETL_HOME/models,target=/models,readonly" --entrypoint python etl-portable-backend:local /opt/etl/model.py verify --directory /models/bge-m3 --manifest /models/bge-m3/etl-model-manifest.json --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
```

Đổi tên image ở mọi lệnh trực tiếp nếu BACKEND_IMAGE có tag khác. Mount `/opt/models/minilm` là tên tương thích trong image, **không có nghĩa dùng MiniLM**; model thật do ID/revision/dimension quyết định. Linux cấp quyền đọc sau khi tải (chỉ thư mục model):

```bash
sudo chown -R 10001:10001 "$ETL_HOME/models"
sudo chmod -R u+rX "$ETL_HOME/models"
```

Runtime không cần ghi model. Tải thêm model sau bước này cần kiểm tra lại quyền đọc của UID 10001.

### 3.4. Secret đăng nhập Docker

Windows Git Bash, thay email thật:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/setup-auth.ps1 -Directory D:/etl/private/auth -AdminEmail YOUR_ADMIN_EMAIL -Image etl-portable-backend:local
```

Linux:

```bash
sudo install -d -o 10001 -g 10001 -m 0700 "$ETL_HOME/private/auth-docker"
ETL_AUTH_SETUP_IMAGE=etl-portable-backend:local bash deploy/scripts/setup-auth.sh "$ETL_HOME/private/auth-docker" YOUR_ADMIN_EMAIL
```

Nhập password ẩn; script in các **đường dẫn** cần chép vào `docker.env`. Linux dùng ba path `auth-docker` vừa in. Có secret thì dùng lại, không ghi đè. Windows có thể dùng lại secret local. `*_SECRET_FILE` là path host Docker, khác `*_FILE` của local.

### 3.5. Docker MP3/MP4 và Argos, tùy chọn

Sửa `docker.env` trước build/start:

```dotenv
MEDIA_ENABLED=true
INSTALL_MEDIA=true
SOURCE_METADATA_MEDIA_ENABLED=false
WHISPER_MODEL_DIR=D:/etl/models/faster-whisper-large-v3
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
# Gợi ý khi host đủ RAM, chạy đồng thời BGE-M3 + large-v3:
API_MEMORY_LIMIT=6g
OCR_MEMORY_LIMIT=10g
```

Launcher tự thêm `compose.media.yml` khi MEDIA_ENABLED=true, mount Whisper read-only tại `/opt/models/whisper`; PowerShell cũng có `-Media`. FFmpeg/Tesseract ở image, thư viện media cần build với extra media.

Tải Whisper bằng container nếu chưa có:

```bash
docker run --rm --user 0:0 -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 -e HF_HUB_DISABLE_XET=1 --mount "type=bind,source=$ETL_HOME/models,target=/models" --entrypoint python etl-portable-backend:local -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Systran/faster-whisper-large-v3', revision='edaa852ec7e145841d8ffdb056a99866b5f0a478', local_dir='/models/faster-whisper-large-v3', allow_patterns=['config.json','model.bin','preprocessor_config.json','tokenizer.json','vocabulary.json'])"
docker run --rm -i --mount "type=bind,source=$ETL_HOME/models,target=/models,readonly" --entrypoint python etl-portable-backend:local - <<'PY'
import hashlib
from pathlib import Path
p = Path('/models/faster-whisper-large-v3')
for n in ('config.json','model.bin','preprocessor_config.json','tokenizer.json','vocabulary.json'):
    assert (p/n).is_file() and (p/n).stat().st_size > 0, n
with (p/'model.bin').open('rb') as f:
    assert hashlib.file_digest(f,'sha256').hexdigest() == '69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1'
print('Whisper files/checksum OK')
PY
```

**Argos:** dùng lệnh curl ở2.3 tải gói vào `$ETL_HOME/models`, giải nén không cần Python host:

```bash
docker run --rm --user 0:0 --mount "type=bind,source=$ETL_HOME/models,target=/models" --entrypoint python etl-portable-backend:local -c "from zipfile import ZipFile; ZipFile('/models/translate-en_vi-1_9.argosmodel').extractall('/models/argos')"
```

Overlay media chưa mount Argos. Muốn bật dịch, tạo file riêng ngoài repo `$ETL_HOME/private/compose.translation.yml`:

```yaml
services:
  api: &translation
    environment:
      TRANSLATION_PROVIDER: argos
      TRANSLATION_MODEL_ROOT: /opt/models/argos
      TRANSLATION_TARGET_LANGUAGE: vi
    volumes:
      - type: bind
        source: ${ARGOS_MODEL_DIR:?Set ARGOS_MODEL_DIR}
        target: /opt/models/argos
        read_only: true
        bind: {create_host_path: false}
  source-file-worker: *translation
  ocr-worker: *translation
```

Thêm `ARGOS_MODEL_DIR=D:/etl/models/argos` vào `docker.env`, dùng mảng APP ở3.6 kèm overlay này. Launcher không tự đọc overlay tùy chỉnh. Không cần dịch: có thể dùng overlay chỉ đặt `TRANSLATION_PROVIDER: disabled`; thiếu gói thì không được coi đã có bản dịch.

### 3.6. Check/start/status/stop và baseline

**Windows Git Bash:**

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Check -EnvFile docker.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -EnvFile docker.env -Workers
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status -EnvFile docker.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Logs -EnvFile docker.env
# Khi cần dừng:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Stop -EnvFile docker.env
```

**Linux:**

```bash
bash deploy/scripts/etl-docker.sh check
bash deploy/scripts/etl-docker.sh start --workers
bash deploy/scripts/etl-docker.sh status
bash deploy/scripts/etl-docker.sh logs
# bash deploy/scripts/etl-docker.sh stop
```

Start tự chạy Check trước khi khởi động; Check riêng dùng để chẩn đoán cấu hình, xác thực, model và DB/schema. Start không build hoặc migration. Có `-Workers`/`--workers` sẽ khởi động **API + frontend + đủ 4 worker**, tổng cộng 6 service ứng dụng. Bỏ cờ này chỉ khởi động API/frontend, không tự dừng các worker đã chạy trước đó. Mở **http://127.0.0.1:8080/login**, Swagger **http://127.0.0.1:8080/docs**; health: `curl -fsS http://127.0.0.1:8080/health`.

| Worker local | Service Docker tương ứng |
| --- | --- |
| `run-master-sync-worker` | `master-sync-worker` |
| `run-source-file-worker` | `source-file-worker` |
| `run-ocr-worker` | `ocr-worker` |
| `data_sync_etl.workers.reconciliation` | `reconciliation-worker` |

Docker Desktop có thể hiển thị nhóm database và nhóm ứng dụng riêng. Bộ `docker-compose.yml` ở gốc dùng cho database local, không khởi động 4 worker ứng dụng. Dùng launcher ở mục này để chạy nhóm ứng dụng; các job `master-migrate`, `source-migrate`, `mongo-init` không phải worker chạy liên tục.

Dùng Drive: thêm **`-Drive` vào mọi lệnh launcher PowerShell**, Linux đặt `export ETL_WITH_DRIVE=true`; cấu hình ở4.2. Media tự chọn từ env, Drive cần lựa chọn launcher riêng.

**Có overlay tùy chỉnh hoặc dùng CLI:** khai báo APP một lần trong terminal Git Bash/Bash; bỏ comment đúng những dòng cần dùng:

```bash
APP=(docker compose --env-file docker.env -f deploy/compose.app.yml -f deploy/compose.auth.yml)
# APP+=(-f deploy/compose.media.yml)
# APP+=(-f deploy/compose.drive.yml)
# APP+=(-f deploy/compose.source-neon.yml)
# APP+=(-f deploy/compose.remote-db.yml)
# APP+=(-f "$ETL_HOME/private/compose.translation.yml")
"${APP[@]}" --profile workers config --quiet
"${APP[@]}" --profile workers config --format json | "${APP[@]}" run --rm --no-deps -T --entrypoint python api /opt/etl/auth_deployment.py
"${APP[@]}" run --rm --no-deps api check-auth
"${APP[@]}" run --rm --no-deps --entrypoint python api /opt/etl/model.py verify
"${APP[@]}" --profile ops run --rm --no-deps preflight
"${APP[@]}" --profile workers up -d --no-build api frontend master-sync-worker source-file-worker ocr-worker reconciliation-worker
"${APP[@]}" --profile workers ps -a
```

**Quản lý riêng worker Docker**, dùng cùng mảng APP và các overlay đã chọn ở trên:

```bash
# Khởi động/recreate đủ 4 worker sau khi đã build image và cấu hình DB/model.
"${APP[@]}" --profile workers up -d --no-build master-sync-worker source-file-worker ocr-worker reconciliation-worker
# Xem cả worker đang chạy và đã thoát; kiểm tra cột State/Status của từng service.
"${APP[@]}" --profile workers ps -a master-sync-worker source-file-worker ocr-worker reconciliation-worker
# Chỉ khởi động worker đối soát:
"${APP[@]}" --profile workers up -d --no-build reconciliation-worker
# Theo dõi log; Ctrl+C chỉ thoát xem log, container vẫn chạy.
"${APP[@]}" --profile workers logs -f --tail 100 master-sync-worker source-file-worker ocr-worker reconciliation-worker
# Dừng riêng worker đối soát, giữ API và các worker khác:
"${APP[@]}" --profile workers stop reconciliation-worker
```

Các tên service đúng là `master-sync-worker`, `source-file-worker`, `ocr-worker`, `reconciliation-worker`. Hai launcher Start với `-Workers`/`--workers` đã chạy đủ bốn service này. Dùng `-Drive`/`ETL_WITH_DRIVE=true` khi nguồn là Drive, kể cả khi chỉ chạy đối soát tệp. Build chỉ tạo image; cần Start/`up -d` để chạy code mới.

Không dùng đồng thời overlay source-neon và remote-db. Bộ tùy chỉnh dùng cùng APP cho build/start/stop, tránh mất mount/env.

Đồng bộ baseline từ UI như2.7. CLI trong container cần nạp runtime secret: `docker exec python ...` đơn thuần không kế thừa DSN mà tiến trình API đã tự nạp:

```bash
app_cli() {
  "${APP[@]}" run --rm --no-deps --entrypoint python api -c "import sys; sys.path.insert(0,'/opt/etl'); from runtime import load_connections; load_connections(); from data_sync_etl.cli.main import main; raise SystemExit(main())" "$@"
}
app_cli sync-master --full --stream all
app_cli source-file-precheck
```

Chạy một lượt bằng đúng container worker, nạp kết nối và kiểm tra boundary như lúc khởi động service:

```bash
worker_once() {
  local worker_service="$1"
  shift
  "${APP[@]}" run --rm --no-deps -T --entrypoint python "$worker_service" - "$@" <<'PY'
import os, sys
sys.path.insert(0, '/opt/etl')
from runtime import verify_runtime
verify_runtime(os.environ['ETL_SERVICE_ROLE'])
if sys.argv[1] == 'reconciliation':
    from data_sync_etl.workers.reconciliation import main
    sys.argv = ['reconciliation', '--once']
    main()
else:
    from data_sync_etl.cli.main import main
    raise SystemExit(main())
PY
}
worker_once master-sync-worker run-master-sync-worker --worker-id master-sync-check --once
worker_once source-file-worker run-source-file-worker --worker-id source-file-check --once
worker_once ocr-worker run-ocr-worker --worker-id ocr-check --once
worker_once reconciliation-worker reconciliation
```

Các lệnh một lượt có thể chạy đồng bộ/xử lý thật. Chỉ cần bấm **Quét luồng này** trên giao diện nếu service đối soát đã chạy liên tục.

**SOURCE demo mới/rỗng**, chỉ nếu bạn chọn dữ liệu thử và đã khởi tạo schema: mount script/fixture/owner secret vào container setup riêng, không cấp owner cho app. `REPO_HOST` dùng Windows Git Bash; Linux dùng `export REPO_HOST="$PWD"`:

```bash
export REPO_HOST="$(pwd -W)"
docker run --rm --network etl-data --mount "type=bind,source=$REPO_HOST/scripts/source_seed.py,target=/setup/seed.py,readonly" --mount "type=bind,source=$REPO_HOST/fixtures/source,target=/fixtures,readonly" --mount "type=bind,source=$ETL_HOME/private/ops-connections.json,target=/setup/ops.json,readonly" --entrypoint python etl-portable-backend:local -c "import json,os,runpy; from pathlib import Path; os.environ.update(json.loads(Path('/setup/ops.json').read_text())); runpy.run_path('/setup/seed.py',run_name='__main__')" --confirm-empty-demo-source --fixture-root /fixtures
app_cli sync-master --full --stream all
```

DB nguồn thật có dữ liệu thì bỏ seed. Không có danh mục thì biểu mẫu chưa chọn được provider/loại. Sau schema/model READY: đặt `INDEX_AUTO_ENABLED=true`, Start/`up -d` lại để recreate theo env; không chỉ `docker restart` container cũ. Sau Full users và Auto users ổn định có thể đổi `AUTH_MODE=master_users` trong `docker.env`, giữ auth profile mặc định rồi Start lại; không dùng profile db-auth cho MASTER users.

### 3.7. Build lại và chuyển máy

Chạy tại gốc repo, dùng cùng `docker.env` và cùng profile/overlay với lần Start trước. Build cập nhật image; Start kiểm tra cấu hình và recreate container dùng image mới, bao gồm cả 4 worker.

**Windows PowerShell hoặc Git Bash:**

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build -EnvFile docker.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -EnvFile docker.env -Workers
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status -EnvFile docker.env
```

**Linux/Bash:**

```bash
bash deploy/scripts/etl-docker.sh build
bash deploy/scripts/etl-docker.sh start --workers
bash deploy/scripts/etl-docker.sh status
```

Nếu dùng Drive, thêm `-Drive` vào cả ba lệnh PowerShell; Bash đặt `export ETL_WITH_DRIVE=true` trước khi chạy. `DATABASE_MODE=source-neon` hoặc `remote` trong `docker.env` được launcher tự chọn; media tự chọn qua `MEDIA_ENABLED=true`. Profile auth tùy chỉnh phải giữ cùng `-AuthProfile` hoặc `ETL_AUTH_PROFILE` ở mọi lệnh.

Nếu dùng mảng APP tùy chỉnh ở mục 3.6, giữ nguyên các overlay và chạy:

```bash
"${APP[@]}" build api frontend
"${APP[@]}" --profile workers config --quiet
"${APP[@]}" --profile workers config --format json | "${APP[@]}" run --rm --no-deps -T --entrypoint python api /opt/etl/auth_deployment.py
"${APP[@]}" run --rm --no-deps api check-auth
"${APP[@]}" run --rm --no-deps --entrypoint python api /opt/etl/model.py verify
"${APP[@]}" --profile ops run --rm --no-deps preflight
"${APP[@]}" --profile workers up -d --no-build api frontend master-sync-worker source-file-worker ocr-worker reconciliation-worker
"${APP[@]}" --profile workers ps -a
```

Sau khi API/frontend healthy, nhấn Ctrl+F5 trên trình duyệt. Sửa code local hoặc chỉ chạy `docker restart` không đưa code mới vào image. Không xóa volume để chữa image cũ.

Xuất/nhập image — Windows Git Bash:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Export -EnvFile docker.env -Archive D:/etl/etl-images.tar
# Máy nhận: clone cùng code, chuẩn bị docker.env/secret/model/DB rồi:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Import -EnvFile docker.env -Archive D:/etl/etl-images.tar
```

Linux: `bash deploy/scripts/etl-docker.sh export /absolute/etl-images.tar`, máy nhận dùng `import` cùng path. Chép cả `.tar.sha256`. Image archive **không có model/secret/DB/storage**: chuyển model kèm manifest, cấp secret, khôi phục DB/storage riêng theo quyền của chủ dữ liệu; sửa host path rồi Check/Start. Máy offline cần có sẵn cả image PostgreSQL/Mongo nếu chạy DB tại đó. Giữ đúng kiến trúc image/host (`linux/amd64` không mặc định native ARM).

## 4. Đổi DB, Drive và chỉ mục nâng cao

### 4.1. DB remote/Neon: sửa cấu hình, không sửa code

| Thành phần | API/worker local | Docker app |
| --- | --- | --- |
| MASTER | `.env`: DATABASE_URL | connections.json: DATABASE_URL |
| SOURCE | `.env`: SOURCE_ADAPTER=postgresql, SOURCE_DATABASE_URL | connections.json: SOURCE_DATABASE_URL |
| Mongo | `.env`: MONGO_URI, MONGO_DATABASE | Hai key tương ứng trong connections.json |
| TLS/profile | CA path host trong URL | docker.env: profile/CA host; JSON dùng CA path container |

`SOURCE_POSTGRES_*`/`POSTGRES_*` chỉ cấu hình container DB local, không chọn runtime khi URL đã trỏ remote. Đổi password DB có volume phải dùng công cụ quản trị DB rồi cập nhật URL, không chỉ sửa env.

**SOURCE Neon, MASTER/Mongo vẫn Docker local:** DBA tạo reader chỉ SELECT trong đúng database, ví dụ `Learning_Resources`. `source_reader.sql` mặc định `etl_source`; sửa câu GRANT CONNECT thành đúng tên, nháy kép nếu giữ chữ hoa. Đặt password bằng công cụ quản trị; không dùng neondb_owner/superuser làm runtime.

```bash
curl -fL https://curl.se/ca/cacert.pem -o "$ETL_HOME/private/neon-ca.pem"
```

Local `.env` (thay endpoint/database/password thật):

```dotenv
SOURCE_ADAPTER=postgresql
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@YOUR_ENDPOINT.neon.tech:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=D%3A%2Fetl%2Fprivate%2Fneon-ca.pem
# Không đặt SOURCE_MIGRATION_DATABASE_URL tới Neon nếu không có công việc owner được duyệt.
```

Docker `docker.env`:

```dotenv
DATABASE_MODE=source-neon
SOURCE_DATABASE_CA_FILE=D:/etl/private/neon-ca.pem
```

Trong connections.json chỉ thay URL SOURCE, giữ MASTER/Mongo nội bộ:

```text
postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@YOUR_ENDPOINT.neon.tech:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=/run/secrets/source-database-ca.pem
```

Launcher tự chọn `compose.source-neon.yml`; APP tùy chỉnh phải thêm overlay này. CA file host khác path container. Chọn endpoint pooled/direct phù hợp trên dashboard Neon, không tự chế hostname.

Nếu chỉ SOURCE đã có trên Neon, còn MASTER/Mongo là DB Docker **mới/trống**, vẫn chuẩn bị password/owner JSON, build image và tạo role MASTER/Mongo theo 3.2. Khởi động riêng hai DB, chạy đúng hai tác vụ sau; không chạy source-migrate/source-seed lên Neon:

```bash
docker compose --env-file docker.env -f deploy/compose.database.yml up -d master-postgres mongo
export ETL_MAINTENANCE_APPROVAL=APPROVED_DATABASE_MAINTENANCE
OPS=(docker compose --env-file docker.env -f deploy/compose.ops.yml --profile ops)
"${OPS[@]}" run --rm ops master-migrate
"${OPS[@]}" run --rm -e INDEX_EMBEDDING_DIMENSION=1024 ops mongo-bootstrap-empty
unset ETL_MAINTENANCE_APPROVAL
```

Owner JSON vẫn cần đủ bốn key; có thể đặt `SOURCE_MIGRATION_DATABASE_URL` thành chuỗi rỗng khi không sử dụng tác vụ SOURCE. Runtime JSON vẫn dùng URL reader Neon. MASTER/Mongo đã có dữ liệu thì dùng quy trình nâng cấp đã được chủ DB duyệt, không khởi tạo lại.

**Tất cả DB remote:** `DATABASE_MODE=remote`, `DATABASE_CA_FILE=/absolute/private/database-ca.pem`; dùng [connections.remote.example.json](deploy/config/connections.remote.example.json). PostgreSQL cần sslmode=verify-full; Mongo cần tls=true và tlsCAFile=/run/secrets/database-ca.pem. DNS/firewall/CA/quyền do quản trị cấp; profile DB không tự mở giao diện ra LAN. Đổi cấu hình cần restart local hoặc Start/recreate Docker.

### 4.2. SOURCE Google Drive

Cần quyền đọc folder/tệp và credential Google hợp lệ: service account được share folder hoặc OAuth authorized_user. Cấp credential theo [DRIVE_SETUP.md](deploy/DRIVE_SETUP.md), không thể suy ra credential từ file ID.

Local `.env`:

```dotenv
SOURCE_DRIVE_ENABLED=true
SOURCE_DRIVE_AUTH_TYPE=authorized_user
SOURCE_DRIVE_CREDENTIALS_FILE=D:/etl/private/drive.json
SOURCE_DRIVE_BUCKETS={"DRIVE":"YOUR_APPROVED_FOLDER_ID"}
SOURCE_LOCAL_BUCKETS=[]
SOURCE_DRIVE_FILENAME_POLICY=canonical_id
SOURCE_DRIVE_FILENAME_ALIASES={}
```

Bucket khớp SOURCE; object key dạng `files/<file_id>/<filename>`. canonical_id cho đổi tên theo file ID đã kiểm chứng và cùng loại tệp; vẫn kiểm tra folder/MIME/quyền/revision/hash. Không cần alias cứng từng file. Muốn kiểm tra tên nghiêm ngặt dùng exact.

Docker thêm `DRIVE_CREDENTIALS_FILE=D:/etl/private/drive.json`, các khóa AUTH_TYPE/BUCKETS/LOCAL_BUCKETS/FILENAME_POLICY tương ứng vào docker.env; image đọc credential qua secret. Thêm `-Drive` vào launcher PowerShell hoặc `ETL_WITH_DRIVE=true` cho Bash. Restart API/source-file/OCR sau thay đổi.

### 4.3. Index, metadata search và semantic chunk

ContentBag cơ bản dùng model1024D và INDEX_AUTO_ENABLED=true sau khi schema/model đúng. Bật auto không có nghĩa đã có index: cần nội dung xử lý xong, OCR worker chạy và INDEX_BUILD hoàn tất. Tìm kiếm vẫn kiểm tra quyền/phiên bản/dẫn chứng, không ép trả top1 khi không có học liệu phù hợp.

Giữ OFF các cờ chưa nghiệm thu trong example: CONCEPT_DYNAMIC_ENABLED, SEARCH_RESULT_SESSIONS_ENABLED, SEARCH_CORPUS_ACCENT_CANDIDATES_ENABLED… Đổi MiniLM384 sang BGE1024 trên DB cũ cần sao lưu, chuyển validator có kiểm soát và xây lại embedding/chỉ mục, không chỉ đổi thư mục rồi dùng vector384.

**Metadata title/description/tags**, tùy chọn: SEARCH_METADATA_ENABLED=true; SEARCH_METADATA_CONTENT_ENABLED=true để kết hợp nội dung. Cần provision/backfill trước, không chỉ bật cờ. CLI ghi metadata hiện chỉ cho DB test cô lập ở development/test, không dùng làm migration production:

```bash
python -m data_sync_etl.cli metadata-search-schema
python -m data_sync_etl.cli metadata-search-diagnostics
# Chỉ đúng DB test riêng đã được phép ghi:
# python -m data_sync_etl.cli metadata-search-schema --apply
# python -m data_sync_etl.cli metadata-search-index --apply --dense
```

SEARCH_METADATA_AUTO_REFRESH_ENABLED=true/SEARCH_METADATA_REFRESH_DENSE=true cần Master Sync Worker và index đã provision; mặc định OFF. Metadata-only media chỉ tìm qua metadata khi nhánh này READY; không giả transcript khi chưa ASR.

**Contextual chunk BGE-M3 song song**, tùy chọn: local cài thêm `python -m pip install -c deploy/docker/constraints.semantic.txt -e '.[dev,embeddings,semantic,google-drive,media,translation]'`. Đặt SEMANTIC_ENABLED=true, SEMANTIC_MODEL_PATH tới BGE-M3 và SEMANTIC_MODEL_REVISION đúng marker. Docker đặt INSTALL_SEMANTIC=true trước build, SEMANTIC_MODEL_DIR tới BGE-M3; chỉ bật SEMANTIC_ENABLED sau schema/backfill được duyệt. Không đổi model/ngưỡng/reranker mù quáng để chữa no-results.

```bash
python -m data_sync_etl.cli semantic-schema
# Chỉ trên môi trường đã được chủ DB cho phép provision/backfill:
# python -m data_sync_etl.cli semantic-schema --apply
# python -m data_sync_etl.cli semantic-index --resource-version-id YOUR_VERSION_ID --apply
python -m data_sync_etl.cli semantic-status --resource-version-id YOUR_VERSION_ID
```

Docker dùng `"${APP[@]}" run --rm --no-deps api semantic-schema`/semantic-status/semantic-index tương ứng. Entry point hỗ trợ các lệnh này; CLI khác dùng app_cli ở3.6.

## 5. Kiểm tra cuối và xử lý lỗi

| Kiểm tra / hiện tượng | Xử lý |
| --- | --- |
| Hoàn tất cài đặt | DB/schema đúng, model verify+encode PASS, API health OK, login được, 4 worker chạy nếu dùng lịch đối soát; thử 1 tệp tới CONTENT_READY/index READY rồi tìm bằng 3 chế độ |
| Thiếu argon2/pymongo… | Kiểm tra venv: `python -c "import sys; print(sys.executable)"`; cài đúng extra, pip check |
| SOURCE_REFERENCE_DISABLED | Sửa đúng .env/docker.env và restart/recreate đúng tiến trình |
| METADATA_ONLY / Chưa xử lý | SOURCE_METADATA_MEDIA_ENABLED=false, đủ model/binary, restart source-file worker; kiểm tra tạo job và OCR worker nhận. PUBLISHED không đồng nghĩa đã trích xuất |
| AUTH_MASTER_STALE | Kiểm tra Auto users/interval/heartbeat/lần COMPLETED; không chỉ watermark |
| Docs trắng/OpenAPI403 | Đăng nhập qua frontend proxy3000/8080; không gọi backend để bỏ boundary |
| SOURCE_HASH_MISMATCH / SOURCE_VERSION_MUTATED | Xác minh bytes/hash/phiên bản ở nguồn; không bỏ kiểm tra hoặc ghi đè lịch sử |
| BLOCKED_INDEX_SCHEMA / NO_READY_INDEX | Đối chiếu dimension/revision/validator và INDEX_BUILD; reindex theo quy trình được duyệt |
| CONNECTIONS_SECRET_VALUES_INVALID | JSON đúng4 key, không placeholder, password/host/database đúng |
| DATABASE_SCHEMA_NOT_READY | Chủ DB kiểm tra đúng database/migration/grants; không tự migrate SOURCE/production |
| MONGO_SCHEMA_NOT_READY | Chạy Check để xác định schema không khớp. Preflight hỗ trợ đúng các schema Base, Lexical và MeaningBag, cùng số chiều model đã cấu hình. Sau khi cập nhật source phải build lại image; schema mở rộng hợp lệ không cần đổi về Base |
| Docker operation failed tại etl-docker.ps1 | Đây là lỗi tổng quát sau khi Docker trả mã lỗi. Xem dòng lỗi hoặc JSON status/code ngay phía trên; chạy lại `-Action Check -EnvFile docker.env` với cùng profile/overlay để chẩn đoán. Start chỉ tiếp tục khi Check PASS |
| SOURCE_READ_ONLY_ROLE_REQUIRED | Reader không superuser/owner/write grants; đúng các bảng SELECT theo script |
| STT_RUNTIME_UNAVAILABLE | Đúng bộ CTranslate2/tokenizer/vocabulary, CPU/int8 và dependency; path local tồn tại |
| Media timeout/OOM | Kiểm tra RAM/cgroup, FFmpeg/ffprobe, thời lượng/kích thước; không tăng đồng thời worker/timeout vô hạn |
| Tìm kiếm lỗi/đổi focus | Nạp frontend mới; xem HTTP/detail.code trong Network. Hết phiên phải login lại; model/index lỗi phải xử lý nguyên nhân, không coi là thiếu tài liệu |
| Docker vẫn bản cũ | Build đúng tag/env/overlay, recreate, Ctrl+F5; không chỉ docker restart và không xóa volume |
| Linux permission denied | UID10001 đọc secret/model; frontend đọc proxy key qua group10001; không chmod777 private |

Migration/seed/các lệnh --apply sẽ ghi DB khi bạn chạy: chỉ dùng đúng môi trường được phép. Không chạy full suite trên DB Dev/Production để kiểm tra cài đặt. LAN/HTTPS cần [TLS/reverse proxy](deploy/README.md), không bật localhost bypass trên LAN.

## Đối soát độc lập

Chạy nhiều worker có giới hạn (mặc định 1, tối đa 2 claim import/audit), khóa theo
resource/stream. Chưa coi triển khai là ACCEPT khi còn
baseline failure hoặc thiếu benchmark toàn pipeline; không tự tăng số worker mặc định.

### Chạy song song có giới hạn

| Cấu hình | Mặc định / tối đa | Phạm vi |
|---|---|---|
| `SOURCE_FILE_IMPORT_MAX_ACTIVE` | 1 / 2 | Tổng job nhập tệp đang giữ lease trong cùng DB; cùng resource vẫn tuần tự |
| `OCR_MAX_ACTIVE` | 1 / 2 | Tổng task OCR đang giữ heartbeat trong cùng DB |
| `MEDIA_MAX_CONCURRENCY` | Giữ 1 cho Whisper | Slot model dùng advisory lock PostgreSQL; không phải số process |
| `RECONCILIATION_MAX_ACTIVE` | 1 / 2 | Tổng job audit; cùng stream vẫn độc quyền |
| `RECONCILIATION_FILE_CONCURRENCY` | 1 / 2 | Một job LIGHT/DEEP: tối đa 2 tệp LOCAL trong một đợt đọc; SOURCE/Drive vẫn 1 |

Opt-in sau khi kiểm tra PostgreSQL contention và đủ RAM/CPU: đặt các cap cần dùng
thành `2` nhất quán ở các process, giữ `MEDIA_MAX_CONCURRENCY=1`, rồi khởi động thêm
worker với ID riêng. Mỗi lệnh sau chạy trong **một terminal riêng**, cùng venv và
môi trường DB test/local đã xác minh:

```bash
python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-a
python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-b
python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-a
python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-b
python -m data_sync_etl.workers.reconciliation
python -m data_sync_etl.workers.reconciliation
```

Hai audit process không cần worker ID CLI; nếu cấu hình health file local thì dùng
`RECONCILIATION_WORKER_HEARTBEAT_FILE` khác nhau. Source/OCR cũng cần
`ETL_WORKER_HEARTBEAT_FILE` riêng nếu dùng health file. Trường health/checkpoint
hiện có không phải registry đếm process; không suy ra số worker khỏe từ một marker.
Không chạy đồng thời các terminal này và bản Docker của cùng worker nếu chưa tính
tổng tải. Hai process với cap 2 vẫn chỉ nhận tối đa 2 job toàn cụm, không thành 4.

Trong một audit, coordinator cấp đúng 2 phần ngân sách byte và đọc kết quả theo thứ
tự ID; chỉ coordinator ghi proof/checkpoint sau khi cả đợt đọc kết thúc. File SOURCE,
kích thước chưa biết hoặc vượt phần ngân sách vẫn xử lý tuần tự. LOCAL không tạo
tệp tạm; không nhân ngân sách byte theo số thread. Không bật mức 3: hiện hard cap là 2.
ASR chờ slot hết thời gian sẽ về PENDING, không tiêu hao retry lỗi trích xuất.

Compose chỉ nhận thêm biến cấu hình với mặc định 1; không tự scale replica. Trước
khi tự scale, giữ đủ overlay, worker ID/health path riêng, RAM/CPU, quota Drive và
temp-disk mỗi container. Giới hạn temp không phải quota đĩa toàn cluster. Benchmark
fixture không thay thế kiểm thử PostgreSQL thật hoặc đo OCR/Whisper thực tế.

Rollback: dừng worker phụ, đặt mọi cap trên về `1`, restart các process còn lại.
Task đang giữ lease hoàn tất hoặc được thu hồi theo cơ chế cũ; không xóa queue,
checkpoint, proof và không đổi lịch đồng bộ/đối soát. MASTER vẫn giữ sync lock và
thứ tự stream; targeted repair không đẩy global checkpoint.

Đối soát dùng các bảng audit có từ migration `0019`; không tự migrate DB đang có dữ liệu. Local mở terminal thứ sáu, kích hoạt venv rồi chạy:

```bash
python -m data_sync_etl.workers.reconciliation
# Kiểm tra một lượt rồi thoát:
# python -m data_sync_etl.workers.reconciliation --once
```

Docker build lại image rồi Start với `-Workers` hoặc `--workers`: launcher chạy thêm `reconciliation-worker`, dùng chung backend image. Worker này không cần nạp BGE-M3/Whisper. Dùng Drive/Neon/remote DB phải giữ overlay tương ứng như API/source-file worker.

Giới hạn mặc định ở `.env.example` và `deploy/docker.env.example`: `RECONCILIATION_MAX_FILES_PER_RUN=100`, `RECONCILIATION_MAX_BYTES_PER_RUN=2147483648`, `RECONCILIATION_MAX_SECONDS_PER_RUN=1800`, `RECONCILIATION_FILE_TIMEOUT_SECONDS=120`. Sửa `.env` cho local hoặc `docker.env` cho Docker rồi restart/recreate các tiến trình liên quan. Hết ngân sách trả PARTIAL và giữ checkpoint; không tăng vô hạn để chữa nguồn mất kết nối.

Trong **Đối soát toàn vẹn dữ liệu → Lịch đối soát theo từng luồng**:

1. Chọn riêng Khối lớp, Môn học, Người dùng, Học liệu hoặc Kho tệp; mặc định lịch đều tắt.
2. Bảng chỉ hiển thị luồng và lịch đang áp dụng. Bấm **Thiết lập** tại một luồng để mở form dùng chung, chọn chế độ/lịch rồi **Lưu**; **Hủy** bỏ thay đổi chưa lưu. Không còn mục cấu hình cũ riêng: các lịch đã lưu được hiển thị trong form thống nhất, giữ chu kỳ/chế độ/bật tắt/repair, và chỉ cập nhật trên server khi bấm Lưu. Nút **Quét đối soát** phía trên vẫn dùng luồng/chế độ đang chọn và không thay đổi lịch.
3. Dropdown trong form cho phép chọn **Thường xuyên (FAST)** dùng metadata Incremental hoặc file LIGHT, gợi ý hằng ngày 02:00; hoặc **Toàn diện (THOROUGH)** dùng metadata Full, gợi ý chủ nhật 03:00, hoặc file DEEP SHA-256, gợi ý ngày 1 mỗi tháng 03:00. Chỉ một nhóm điều khiển hiển thị mỗi lần; hai lịch vẫn có thể bật đồng thời và giữ cấu hình riêng. Các gợi ý đều tắt và không tự bật repair. Cả hai nhóm đều chọn được Phút/giờ (1/5/10/15/20/30 phút hoặc 1 tiếng), Ngày/Tuần/Tháng.
4. Mỗi nhánh chọn Ngày/Tuần/Tháng, thứ/ngày (1–28), giờ/phút và múi giờ IANA (mặc định `Asia/Ho_Chi_Minh`). Tháng tính theo lịch thực, không phải 30 ngày; **Lần tiếp theo** do server tính. Worker chỉ nhận kỳ hiện tại đến hạn, không bù toàn bộ kỳ đã bỏ lỡ. Lịch IDS cũ được giữ khi scheduler mặc định bật; nếu tắt scheduler mặc định thì lịch THOROUGH metadata kèm IDS cùng kỳ.
5. Với metadata, bật **Tự đồng bộ khi lệch** riêng từng lịch để cập nhật đúng ID đủ điều kiện; tắt để chỉ đọc/báo cáo. Không tự xóa bản ghi mồ côi, không ghi đè học liệu LOCAL hoặc vượt kiểm tra ownership.
6. Kho tệp LIGHT chỉ kiểm tra metadata/kích thước, không tải blob và không tính là đã xác minh SHA-256. THOROUGH Full/DEEP tiếp tục các batch của cùng kỳ từ checkpoint, có khoảng nghỉ theo backoff hiện có. Duyệt hết chu kỳ nhưng còn file chưa xác minh vẫn báo PARTIAL; không tuyên bố toàn bộ kho verified. Sai hash hoặc nguồn chưa truy xuất được không tự sửa hash hay ghi đè phiên bản.

Hai nút **Đồng bộ Full** và **Chạy Incremental** thủ công vẫn ở trang Đồng bộ dữ liệu. Auto Master Sync chạy trực tiếp theo cấu hình riêng, không chờ đối soát. Lịch audit chạy ở reconciliation worker; tắt lịch không hủy tác vụ thủ công hoặc xóa lịch sử. `RECONCILIATION_SCHEDULER_ENABLED=false` chỉ tắt bộ lịch nền định sẵn cũ; lịch do admin lưu trên giao diện vẫn được worker riêng thực thi.

`AUTH_MODE=master_users` vẫn kiểm tra độ mới. Auto Sync users phải bật với chu kỳ ngắn hơn giới hạn freshness và Master Sync Worker phải chạy; lịch audit ngày/tuần không thay trách nhiệm này. Partial repair hoặc audit delta không tự làm mới bằng chứng auth toàn diện. Audit Full users chỉ được dùng khi xác minh đầy đủ trường auth và tập ID SOURCE/MASTER; không tự nới freshness để bỏ kiểm tra.

Các bảng tải dữ liệu theo trang; tổng số trang tệp được yêu cầu khi chuyển trang/bộ lọc, không đếm lại ở mỗi lần polling. Polling quản trị dừng khi tab ẩn.

### Worker từ điển tùy chọn: chỉ thử nghiệm offline

CLI `concepts run-worker` dùng fixture tổng hợp, không nằm trong bốn service runtime ở trên. Giữ các cờ từ điển chưa nghiệm thu ở OFF. Nếu cần thử riêng, chọn thư mục mới/rỗng và dùng venv đã cài dependencies:

```bash
python -m data_sync_etl.cli.concepts example --root "$ETL_HOME/concept-offline-example"
python -m data_sync_etl.cli.concepts run-worker --root "$ETL_HOME/concept-offline-example" --fixture "$ETL_HOME/concept-offline-example/example-corpus.json" --apply --loop
```

Lệnh `example` chỉ chạy một lần, từ chối ghi đè fixture có sẵn. Terminal khác kiểm tra bằng:

```bash
python -m data_sync_etl.cli.concepts health --root "$ETL_HOME/concept-offline-example"
```

Bỏ `--loop` để chạy một tick; bỏ `--apply` ở `run-worker` để xem dry-run. Chế độ `--live` hiện trả `BLOCKED_LIVE`; ví dụ offline không chứng minh từ điển live đã sẵn sàng.

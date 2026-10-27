# ETL — Kho học liệu số

Mở rộng MP3/MP4, phụ đề, OCR video và nguyên ngữ/bản dịch: xem [cài đặt và API](MEDIA_PIPELINE.md), [kết quả kiểm thử và giới hạn](MEDIA_ACCEPTANCE.md).

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

### 1.3a. Cài môi trường MP3/MP4, Whisper và dịch cục bộ

Thực hiện tại gốc repo, trong venv của mục 1.1. Máy đã có model/binary hợp lệ có thể bỏ qua bước tải. Cần FFmpeg **và ffprobe**, Tesseract `vie+eng` ở mục 1.2, Whisper đa ngôn ngữ và gói dịch đúng cặp ngôn ngữ. Model chỉ tải lúc cài đặt; API/worker không tự tải hoặc gửi tài liệu sang dịch vụ dịch bên ngoài.

```bash
python -m pip install -e ".[dev,embeddings,google-drive,media,translation]"
python -m pip check
```

Extra `media` pin faster-whisper 1.2.1 và PyAV `<17`; không tự nâng PyAV riêng vì API giải mã bản mới không tương thích. Cấu hình đã kiểm thử dùng CPU/int8, 4 threads và 1 slot media, phù hợp máy RAM khoảng 16 GB. GPU là tùy chọn, cần môi trường CUDA tương thích riêng.

**FFmpeg — PowerShell Windows:**

```powershell
winget install --id Gyan.FFmpeg --exact
# Mở lại terminal sau khi cài để cập nhật PATH.
ffmpeg -version
ffprobe -version
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
```

**Ubuntu/Debian:**

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng
ffmpeg -version
ffprobe -version
tesseract --list-langs
```

**Tải Whisper large-v3 đầy đủ, đa ngôn ngữ (CTranslate2)**. Checkpoint [Systran/faster-whisper-large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) chuyển từ `openai/whisper-large-v3`, không phải turbo/distil/English-only. Dùng [faster-whisper 1.2.1](https://github.com/SYSTRAN/faster-whisper/tree/v1.2.1) hiện có; không cần nâng dependency hay cài CUDA. Large-v3 ưu tiên chất lượng và có thể chậm hơn small; GPU 4 GB không được mặc định là đủ.

Chạy Git Bash hoặc PowerShell tại gốc repo bằng Python `.venv`. `huggingface_hub` đã là dependency của faster-whisper; chỉ cài nếu kiểm tra import báo thiếu. Tải cần Internet một lần, runtime chỉ dùng local. Bộ inference bên dưới khoảng **3,09 GB**; kiểm tra dung lượng trống trước khi tải. Base/small trên máy hiện tại đã được xóa theo yêu cầu; chỉ giữ thư mục large-v3.

**Git Bash — tải model:**

```bash
cd /d/HK7/KLTN/ETL
unset HF_HUB_OFFLINE
export HF_HUB_DISABLE_XET=1
./.venv/Scripts/python.exe -c "import huggingface_hub"
# Chỉ khi import ở trên báo thiếu:
# ./.venv/Scripts/python.exe -m pip install huggingface_hub
./.venv/Scripts/python.exe -c "import shutil; print('Free bytes:', shutil.disk_usage('.').free)"
./.venv/Scripts/python.exe - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="Systran/faster-whisper-large-v3",
    revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
    local_dir="Models/faster-whisper-large-v3",
    allow_patterns=["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"],
)
PY
```

Sau khi lệnh tải kết thúc thành công, kiểm tra đủ tệp và checksum trong Git Bash:

```bash
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
import hashlib

p = Path("Models/faster-whisper-large-v3")
for name in ["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]:
    assert (p / name).is_file() and (p / name).stat().st_size > 0, name
with (p / "model.bin").open("rb") as f:
    checksum = hashlib.file_digest(f, "sha256").hexdigest()
assert checksum == "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1", checksum
print("Checksum OK:", checksum)
PY
HF_HUB_OFFLINE=1 ./.venv/Scripts/python.exe scripts/evaluate_media_asr.py --model Models/faster-whisper-large-v3 --languages vi_vn fr_fr ja_jp --report .test-tmp/asr-large-v3.json
```

Lệnh evaluator cần các mẫu audio/reference local nêu bên dưới. Khi kiểm tra thành công, restart API/worker để nạp cấu hình model mới.

**PowerShell — lệnh tương đương:**

```powershell
.\.venv\Scripts\python.exe -c "import huggingface_hub"
# Chỉ khi import ở trên báo thiếu:
# .\.venv\Scripts\python.exe -m pip install huggingface_hub
.\.venv\Scripts\python.exe -c "import shutil; print('Free bytes:', shutil.disk_usage('.').free)"
Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
$env:HF_HUB_DISABLE_XET="1"
@'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="Systran/faster-whisper-large-v3",
    revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
    local_dir="Models/faster-whisper-large-v3",
    allow_patterns=["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"],
)
'@ | .\.venv\Scripts\python.exe -
```

Revision và danh sách tệp được xác minh từ Hugging Face ngày 26/10/2026. Chạy lại cùng lệnh để tận dụng tệp/cache đã tải và tiếp tục phần thiếu khi Hub hỗ trợ; không xóa cache trong lúc tải. Không lấy trọng số PyTorch. Model/cache được `.gitignore` loại trừ.

Model large-v3 trên máy hiện tại đã tải đủ 3.087.284.237 byte weights, checksum đã khớp và evaluator offline CPU/int8 đã chạy thành công trên ba mẫu local. Kết quả và giới hạn nằm trong MEDIA_ACCEPTANCE; chưa kiểm chứng video 30 phút. Nếu đường Xet đứng khi cài máy khác, đặt `$env:HF_HUB_DISABLE_XET="1"` trong PowerShell rồi chạy lại lệnh tải; HTTP cũng có thể bị gián đoạn theo kết nối.

**Kiểm tra sau tải:** có thư mục chưa có nghĩa model đã sẵn sàng. Kiểm tra đủ năm tệp, checksum rồi nạp CPU/int8 thật; lệnh cuối dùng mẫu speech local đã có, không truy cập DB:

```powershell
@'
from pathlib import Path
import hashlib
p = Path("Models/faster-whisper-large-v3")
for name in ["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]:
    assert (p / name).is_file() and (p / name).stat().st_size > 0, name
with (p / "model.bin").open("rb") as f:
    checksum = hashlib.file_digest(f, "sha256").hexdigest()
assert checksum == "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1", checksum
print("large-v3 edaa852ec7e145841d8ffdb056a99866b5f0a478", checksum)
'@ | .\.venv\Scripts\python.exe -
$env:HF_HUB_OFFLINE="1"
.\.venv\Scripts\python.exe scripts/evaluate_media_asr.py --model Models/faster-whisper-large-v3 --languages vi_vn fr_fr ja_jp --report .test-tmp/asr-large-v3.json
```

Lệnh đánh giá cần audio/reference local theo [MEDIA_ACCEPTANCE.md](MEDIA_ACCEPTANCE.md); báo cáo gồm transcript, timestamp, WER/CER, thời gian cold start, thời gian nạp model và revision nếu có metadata tải. Evaluator dùng beam=3, cửa sổ VAD 15 giây, không điều kiện hóa trên văn bản trước. Muốn so sánh lại với small phải tải lại small trước; kết quả small trước khi xóa vẫn là bằng chứng lịch sử. Đây là kiểm tra mẫu ngắn, không thay thế pipeline video 30 phút. Runtime từ chối bộ model thiếu tokenizer để tránh tải ngầm và không tự fallback sang small. BGE-M3 và Argos không thay đổi.

**Tải và giải nén gói Argos Anh → Việt 1.9** vào thư mục model riêng:

```bash
mkdir -p Models/argos .test-tmp/media-setup
python -c "from pathlib import Path; from urllib.request import urlretrieve; from zipfile import ZipFile; p=Path('.test-tmp/media-setup/translate-en_vi-1_9.argosmodel'); urlretrieve('https://argos-net.com/v1/translate-en_vi-1_9.argosmodel',p); ZipFile(p).extractall('Models/argos')"
```

Kết quả phải có `Models/argos/translate-en_vi-1_9/metadata.json`, tokenizer và `model/model.bin`. `TRANSLATION_MODEL_ROOT` trỏ tới **thư mục cha `Models/argos`**, không trỏ vào một gói con. Cặp nguồn/đích khác cần gói tương ứng; chỉ gói Anh → Việt ở trên đã được kiểm chứng. Thiếu gói dịch không làm mất nguyên ngữ.

**Thêm một lần vào `.env`**, đổi đường dẫn nếu đặt model ở nơi khác:

```dotenv
# Binary có trong PATH; cũng có thể dùng đường dẫn tuyệt đối tới từng .exe.
FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe
# Đường dẫn tương đối được tính từ gốc repo khi khởi động API/worker.
WHISPER_MODEL_PATH=Models/faster-whisper-large-v3
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
TRANSLATION_PROVIDER=argos
TRANSLATION_MODEL_ROOT=Models/argos
TRANSLATION_TARGET_LANGUAGE=vi
# false: SOURCE tự xếp hàng xử lý nội dung; true: giữ chế độ đăng ký metadata-only.
SOURCE_METADATA_MEDIA_ENABLED=false
```

Giữ `OCR_ENGINE=tesseract`, `TESSERACT_PATH` và `TESSERACT_LANGUAGES=vie+eng` ở phần OCR đã có, **không khai báo lại các biến này ở cuối file**. Đường dẫn Windows có khoảng trắng nên đặt trong dấu nháy. Các đường dẫn `.test-tmp/media-tools/...` đã dùng để kiểm thử vẫn chạy nếu còn tệp, nhưng không nên dùng thư mục test làm nơi cài công cụ lâu dài.

Giới hạn mặc định: 50 MiB, 30 phút/tệp, timeout media 900 giây, tối đa 120 khung/5.000 đoạn, dịch 60 giây/đoạn và 600 giây/tài liệu. Có thể đặt rõ các biến `MEDIA_*`, `ASR_WINDOW_SECONDS`, `TRANSLATION_*` theo `.env.example`; không bắt buộc thêm tất cả nếu dùng mặc định.

**Schema và chạy:** sau khi tích hợp `main`, phần media dùng MASTER revision `0018`, nối tiếp `0017` xóa mềm. Với DB theo lịch sử `main`, kiểm tra đúng MASTER local/dev, sao lưu và dừng API/worker trước khi nâng; không chạy lệnh nâng trên SOURCE hoặc production:

```bash
python -m alembic current
# Sau khi kiểm tra lịch sử migration và chuẩn bị như trên:
python -m alembic upgrade 0018
```

DB từng áp dụng migration media cũ mang revision `0017` trước khi merge cần đối chiếu schema/lịch sử riêng: revision đó trùng số với migration xóa mềm trên `main`. Không chạy upgrade hoặc stamp mù trên DB này; giữ các cột/dữ liệu media đã có. Lượt merge chỉ kiểm thử migration trên DB cô lập, không sửa DB đang phục vụ.

Đường dẫn tương đối tính từ thư mục làm việc của API/worker; nên chạy tại gốc repo. Máy hiện tại dùng `WHISPER_MODEL_PATH=D:/HK7/KLTN/ETL/Models/faster-whisper-large-v3`. Bỏ cấu hình model nếu chưa cài: mặc định `None` trả lỗi ASR có kiểm soát, không tải mạng.

Khởi động lại API và worker OCR theo mục 1.7 để nạp `.env` và model: Ctrl+C từng tiến trình, sau đó chạy lại trong hai terminal PowerShell tại gốc repo:

```powershell
.\.venv\Scripts\python.exe -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
# Terminal thứ hai:
.\.venv\Scripts\python.exe -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1
```

Quay lại small: máy hiện tại đã xóa small nên cần tải/cài lại model này trước khi đổi `WHISPER_MODEL_PATH=Models/faster-whisper-small`, giữ CPU/int8 rồi restart hai tiến trình như trên; không xóa large-v3. Không cần migration cho việc đổi model. API, lựa chọn track/ngôn ngữ, xem nguyên ngữ/bản dịch và xuất kết quả: [MEDIA_PIPELINE.md](MEDIA_PIPELINE.md). Kết quả kiểm chứng và giới hạn: [MEDIA_ACCEPTANCE.md](MEDIA_ACCEPTANCE.md).

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

Nếu tên trong tham chiếu SOURCE cũ khác tên thật trên Drive, ưu tiên sửa metadata ở hệ thống nguồn. Khi SOURCE chỉ đọc, có thể cấu hình `SOURCE_DRIVE_FILENAME_ALIASES` bằng JSON ánh xạ chính xác `files/<file_id>/<tên SOURCE>` sang tên thật đã kiểm chứng; mặc định `{}`. Ánh xạ chỉ áp dụng đúng file ID và tên nguồn đó, phải cùng phần mở rộng, không chứa đường dẫn. Vẫn kiểm tra thư mục Drive được phép, MIME, kích thước, revision và SHA-256 so với SOURCE; không bật cơ chế so tên gần đúng. Restart API, source-file worker và OCR worker sau khi đổi cấu hình. Không tự bỏ qua `SOURCE_VERSION_MUTATED`: với bản ghi chưa có phiên bản đích/job, chỉ phục hồi sau khi xác minh lại bytes/hash và lưu bản ghi trước sửa; phiên bản đã nhập phải giữ kiểm tra bất biến.

### 1.5. Chuẩn bị DB

**DB có schema sẵn:** điền URL ở trên, không chạy lại nhóm khởi tạo DB rỗng bên dưới. Riêng việc bổ sung media cần kiểm tra/nâng MASTER theo mục 1.3a. Nếu DB local đã tạo nhưng đang dừng:

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

Với `master_users`, bật luồng Auto users và giữ Master Sync Worker chạy. Worker ưu tiên users, làm mới thêm tối đa một lần trong lượt đồng bộ dài; không tự bật luồng đang tắt. Khoảng cách giữa hai lần users thành công phải dưới `AUTH_MASTER_FRESHNESS_SECONDS` (mặc định 120 giây), tính cả thời gian xử lý và chờ lịch. `AUTH_MASTER_STALE` trả 503 khi dữ liệu xác thực quá cũ; kiểm tra worker/lịch users thay vì tắt kiểm tra freshness. Khi cập nhật code worker, khởi động lại tiến trình worker để nạp bản sửa.

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

Quản trị viên có thể xóa tại **Kho học liệu → Chi tiết → Xóa học liệu**, bên dưới nút **Cập nhật thông tin**; mở **Danh sách học liệu → Đã xóa → Khôi phục** để đưa học liệu trở lại. Cần `ADMIN_ENABLED=true` và vai trò ADMIN. Thao tác xóa mềm giữ nguyên file và dữ liệu nguồn; khôi phục vẫn kiểm tra trạng thái tại nguồn.

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

BoW yêu cầu bằng chứng về khái niệm đã nhận diện; từ phụ như “hai” không đủ để nhận một truy vấn nhiều từ. Nhánh metadata dùng cùng quy tắc và `SEARCH_BOW_MIN_COVERAGE`. Với không dấu, dùng `SEARCH_APPROVED_ACCENT_VARIANTS_ENABLED=true`; không tự phê duyệt bản dịch hoặc sửa từ điển seed.

Vector giữ `SEARCH_VECTOR_MIN_SIMILARITY=0.6`. Đường bổ trợ mặc định `SEARCH_VECTOR_ANCHOR_FALLBACK_ENABLED=true`, `SEARCH_VECTOR_ANCHOR_MIN_SIMILARITY=0.4` chỉ nhận điểm dense dưới ngưỡng chính khi đoạn nguồn đã xác minh có cụm định danh nhiều từ hoặc khái niệm tương đương đã duyệt. Điểm xếp hạng vẫn là cosine; không cần model mới hoặc reindex. Cấu hình ở `.env` local / `docker.env`; restart API hoặc tạo lại container. Tắt cờ bổ trợ để quay về ngưỡng chính.

`branch_status.metadata=METADATA_SCHEMA_NOT_READY` là chỉ mục metadata chưa được provision hợp lệ, không phải vector nội dung bị hỏng. Kiểm tra bằng `python -m data_sync_etl.cli metadata-search-schema` (không `--apply`); mọi thay đổi collection/schema cần quyền và phê duyệt riêng. Không hạ ngưỡng hoặc tự tạo schema để che trạng thái này.

Từ điển dẫn xuất từ học liệu có luồng thử nghiệm **offline**, không tạo collection MongoDB hoặc sửa schema MASTER. Các cờ `CONCEPT_DYNAMIC_ENABLED`, `CONCEPT_DYNAMIC_BUILDER_ENABLED`, `CONCEPT_DISCOVERY_SEMANTIC_ENABLED` mặc định `false`; bật cờ không thay thế xác minh quyền đọc, thu hồi quyền và artifact volume. Hiện chưa cho phép đọc corpus/publish dùng chung trên hệ thống thật.

Thử bằng dữ liệu giả lập, không tải model hoặc kết nối DB:

```powershell
$conceptRoot = Join-Path (Get-Location) '.test-tmp/concept-demo'
python -m data_sync_etl.cli.concepts example --root $conceptRoot
python -m data_sync_etl.cli.concepts run --root $conceptRoot --fixture "$conceptRoot/example-corpus.json" --loop
# Ctrl+C để dừng; xem các thao tác duyệt/build/publish offline bằng --help.
python -m data_sync_etl.cli.concepts --help
```

Hướng dẫn đầy đủ: [từ điển corpus offline](deploy/CONCEPT_DICTIONARY_OFFLINE.md). Chỉ quan hệ corpus đã duyệt, còn hiệu lực và đủ quyền mới được mở rộng truy vấn lexical. Khi từ điển tắt hoặc chưa sẵn sàng, hệ thống tìm bằng câu truy vấn gốc; không dùng từ điển tĩnh dự phòng.

Thử nghiệm khôi phục dấu từ cụm trong ContentBag có cờ riêng `SEARCH_CORPUS_ACCENT_CANDIDATES_ENABLED=false` trong `.env.example` và `deploy/docker.env.example`. Cờ mặc định tắt; chỉ bật trong môi trường kiểm thử sau khi kiểm chứng chất lượng trên dữ liệu của bạn. Vector luôn dùng câu truy vấn gốc.

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

Xóa mềm học liệu (cần migration MASTER `0017` được phê duyệt): admin đã đăng nhập với `ADMIN_ENABLED=true` gọi `DELETE /api/v1/resources/{id}`; khôi phục bằng `POST /api/v1/resources/{id}/restore`. JSON tùy chọn: `{"reason":"Lý do","expected_revision":0}`; lấy revision mới từ response cho thao tác tiếp theo. Dùng session/CSRF như các lệnh quản trị khác. Xóa chỉ đặt visibility override tại MASTER, không sửa SOURCE hay xóa tệp/nội dung; không hỗ trợ xóa riêng phiên bản. Restore không đổi trạng thái xuất bản và cần xác minh/xây lại thế hệ chỉ mục trước khi tìm kiếm lại. Kết quả tìm kiếm được xác minh lại khi điều hướng/trở lại tab; trạng thái bộ lọc vẫn giữ trong URL. Không tự chạy migration trên DB đang phục vụ.

Sau khi chỉ mục đã sẵn sàng và được phép ghi, có thể bật `SEARCH_METADATA_AUTO_REFRESH_ENABLED=true` trong `.env`/`docker.env` và chạy **Master Sync Worker** hiện có; thêm `SEARCH_METADATA_REFRESH_DENSE=true` nếu cần cập nhật vector. Worker quét mặc định 5 học liệu mỗi 30 giây, lưu checkpoint và retry; không OCR lại. Các cờ tự động mặc định `false`. Xem số lượng trạng thái và thời điểm hoàn tất vòng quét bằng `python -m data_sync_etl.cli metadata-search-diagnostics`; đây là số liệu storage, không phải số học liệu người dùng được phép xem.

Thử nghiệm kho metadata trên 200 học liệu: `SEARCH_METADATA_INDEXED_CANDIDATES_ENABLED=true`, giới hạn authority `SEARCH_METADATA_AUTHORITY_LIMIT=10000`, giới hạn kết quả khớp `SEARCH_METADATA_MATCH_LIMIT=2000`. Cần provision chỉ mục ứng viên và chạy refresh/backfill trước. Kiểm tra bằng `python -m data_sync_etl.cli metadata-search-schema --candidates`; lệnh thêm `--apply` chỉ cho DB test cô lập trong development/test. Không chạy lệnh ghi trên DB đang phục vụ. Truy vấn từ vựng dùng inverted index nhưng vẫn đọc authority theo lô để xác minh quyền và độ mới; dense trên 200 vẫn báo `BLOCKED_SCALE`, không dùng danh sách từ khóa thay cho vector. Mặc định cờ này `false`; tắt lại để rollback.

Nếu đang dùng contextual content embedding, có thể thử `SEMANTIC_INPUT_IDENTITY_ENABLED=true` để nhận diện thế hệ từ đúng đầu vào embedding, và `SEARCH_METADATA_REFRESH_CONTENT_SEMANTIC=true` để worker cập nhật các phiên bản đã từng lập semantic index khi metadata ngữ cảnh thay đổi. Cần `SEMANTIC_ENABLED=true` và model offline sẵn sàng; không tự OCR hoặc lập semantic index lần đầu. Cả hai cờ mặc định `false`; khi đổi cờ identity cần xây lại thế hệ tương ứng. Chưa nghiệm thu chất lượng production; không điều chỉnh ngưỡng từ bộ nhãn draft.

Đối soát **Kho tệp** chạy nền: API trả `202`, giao diện cập nhật tiến độ từng tệp mỗi 2 giây; có thể rời trang mà không hủy lượt quét. `SOURCE_READ_TIMEOUT_SECONDS` (mặc định `120`) giới hạn riêng từng tệp, không giới hạn cả kho trong 30 giây. Tệp lỗi/hết thời gian được ghi **Chưa xác minh**, các tệp khác vẫn tiếp tục. Nếu API bị dừng, mở lại trang và bấm **Quét đối soát** để ưu tiên phần chưa quét; không tái sử dụng hash cũ làm kết quả xác minh mới. Chỉ ghi nhật ký đối soát, không sửa dữ liệu học liệu hoặc lưu bản sao SOURCE lâu dài.

Đổi password admin cấu hình: `python -m data_sync_etl.security.cli hash --output D:/etl/private/auth/admin_password_hash_v2`, đổi đường dẫn secret rồi restart. Password user MASTER đổi ở SOURCE rồi đồng bộ.

Kiểm thử fixture, không cần DB đang phục vụ:

```bash
python -m pytest tests/unit -q
node --experimental-strip-types --test --test-concurrency=1 frontend/tests/*.test.mjs
npm.cmd --prefix frontend run build
```

Hợp đồng schema ở `fixtures/contracts/`; truy vấn/nhãn đánh giá ở `fixtures/search/`. Nhãn draft chưa được nghiệm thu. Báo cáo sinh mới đặt trong `.test-tmp/` hoặc `artifacts/`; `docs/` và `research/` chỉ giữ ở máy, không thuộc cây source Git.

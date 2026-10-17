# ETL — Đồng bộ và xử lý học liệu

Đồng bộ metadata PostgreSQL nguồn → PostgreSQL Master, nhập file Local/Google Drive → trích xuất/OCR → chuẩn hóa → phân đoạn, ContentBag và embedding → tìm kiếm BoW/Vector/Hybrid → thực nghiệm đánh giá.

Hỗ trợ **PDF, DOCX, XLSX, TXT, CSV, PPTX, PNG, JPG, JPEG**; không hỗ trợ MP3/MP4.

Hướng dẫn dành cho **Windows + Git Bash, môi trường Development**. Docker chạy database; API, giao diện và worker chạy trên máy. Cài mới theo mục 1–7; lần sau xem mục 9. Google Drive là tùy chọn, không cần nếu chỉ dùng Local.

## 1. Cài môi trường và dependencies

Cài Git, Python **3.12+**, Node.js **22.12+** và Docker Desktop. Mở Docker Desktop, sau đó mở Git Bash tại thư mục repository có `pyproject.toml`.

```bash
python --version
node --version
docker compose version

# Chỉ tạo môi trường ở lần cài đầu
python -m venv .venv
source .venv/Scripts/activate
python -X utf8 -m pip install --upgrade pip
python -X utf8 -m pip install -e ".[dev,embeddings]"
npm.cmd --prefix frontend ci

# Không ghi đè cấu hình cá nhân đã có
test -f .env || cp .env.example .env
```

PowerShell kích hoạt bằng `.\.venv\Scripts\Activate.ps1`; các đoạn `python - <<'PY'` bên dưới phải chạy bằng **Git Bash**. Extra `embeddings` cài runtime, chưa tải model.

## 2. Cấu hình `.env`

Sửa các giá trị sau trong `.env`; giữ các nhóm còn lại theo `.env.example`. Đường dẫn chỉ là ví dụ, thay theo máy của bạn.

```dotenv
APP_ENVIRONMENT=development
ADMIN_ENABLED=true

# PostgreSQL Master
POSTGRES_USER=etl
POSTGRES_DB=etl
POSTGRES_PASSWORD=your-master-password
DATABASE_URL=postgresql+psycopg://etl:your-master-password@127.0.0.1:5432/etl

# PostgreSQL nguồn: owner chạy migration, reader chỉ đọc
SOURCE_ADAPTER=postgresql
SOURCE_POSTGRES_USER=etl_source_admin
SOURCE_POSTGRES_DB=etl_source
SOURCE_POSTGRES_PASSWORD=your-source-owner-password
SOURCE_MIGRATION_DATABASE_URL=postgresql+psycopg://etl_source_admin:your-source-owner-password@127.0.0.1:5433/etl_source
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:your-source-reader-password@127.0.0.1:5433/etl_source

# Kho file nguồn và kho ETL tách biệt
SOURCE_FILE_STORAGE_ROOT=D:/SourceLearningFiles
STORAGE_ROOT=D:/ETLStorage
SOURCE_FILE_ALLOWED_STATUSES=["APPROVED"]

# MongoDB dùng chung, đúng 5 collection
MONGO_URI=mongodb://127.0.0.1:27018
MONGO_DATABASE=etl_content
MONGO_METADATA_MODE=shared
SOURCE_MONGO_ENABLED=false
MASTER_MONGO_IDENTITY_BOOTSTRAP=false

# Chỉ bật sau khi kiểm tra schema/model
INDEX_AUTO_ENABLED=false
INDEX_EMBEDDING_DIMENSION=384

# Local không cần Drive
SOURCE_DRIVE_ENABLED=false
SOURCE_DRIVE_BUCKETS={}
SEARCH_INTERNAL_DEV_ENABLED=false
```

```bash
mkdir -p /d/SourceLearningFiles /d/ETLStorage
```

- Thay cả ba mật khẩu mẫu; mật khẩu trong URL phải khớp role tương ứng. Dùng mật khẩu dài gồm chữ/số/gạch ngang để dễ cấu hình; ký tự đặc biệt trong URL cần percent-encode và lưu ý nội suy của Compose.
- Database đã tồn tại: sửa `.env` **không đổi mật khẩu PostgreSQL**. Dùng thông tin thực tế, không xóa volume để đặt lại.
- File Local được đọc từ `<SOURCE_FILE_STORAGE_ROOT>/<storage_bucket>/<storage_object_key>`. Hai kho nguồn/ETL không được trùng hoặc lồng nhau.
- `ADMIN_ENABLED` không thay thế xác thực quản trị; chỉ bật trên máy Dev được kiểm soát. Không đưa `.env`, credential, token, model hoặc học liệu vào Git.

## 3. Khởi tạo database

Chỉ chạy trên database Dev đã xác nhận. Với database có dữ liệu, sao lưu và duyệt migration trước khi cập nhật.

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
python -m data_sync_etl.adapters.mongo.schema --check
```

| Database | Địa chỉ trên máy |
| --- | --- |
| PostgreSQL Master | `127.0.0.1:5432/etl` |
| PostgreSQL nguồn | `127.0.0.1:5433/etl_source` |
| MongoDB | `127.0.0.1:27018/etl_content` |

Ba tiến trình khởi tạo `master-migrate`, `source-migrate`, `mongo-init` cần kết thúc mã **0**. MongoDB cần `schema_ready=true`, đủ `users`, `learning_resources`, `ocr_pages`, `mongo_content_units`, `content_bags` và 6 index nghiệp vụ; embedding 384 chiều.

Database mới trống được tạo schema tự động, **không tự có học liệu mẫu**. Database cũ sai schema sẽ bị chặn, không tự sửa. Không chạy `--apply` khi chưa backup/phê duyệt; không dùng `docker compose down -v` để chữa lỗi.

### Tạo reader nguồn — một lần khi cài mới

Sau khi `source-migrate` thành công, chạy với tên database/user như mục 2:

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong `psql`, nhập:

```text
\password etl_source_reader
\q
```

Nhập mật khẩu reader theo yêu cầu, khớp `SOURCE_DATABASE_URL`. Nếu đổi tên database, đối chiếu cả `GRANT CONNECT` trong script.

## 4. Cài Tesseract OCR

Cần cho ảnh và PDF scan; `pip install` không cài phần mềm này.

1. Mở [hướng dẫn Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html), theo liên kết bản Windows của UB Mannheim và cài đặt.
2. Chọn dữ liệu ngôn ngữ **English + Vietnamese**. Nếu thiếu, tải `eng.traineddata`, `vie.traineddata` từ [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast), chép vào thư mục `tessdata` của bản cài.
3. Kiểm tra đường dẫn thực tế, ví dụ:

```bash
"/c/Program Files/Tesseract-OCR/tesseract.exe" --version
"/c/Program Files/Tesseract-OCR/tesseract.exe" --list-langs
```

Danh sách cần có `eng` và `vie`. Cấu hình `.env`:

```dotenv
OCR_ENGINE=tesseract
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSERACT_LANGUAGES=vie+eng
OCR_TIMEOUT_SECONDS=60
```

Kiểm tra runtime theo cấu hình Backend:

```bash
python - <<'PY'
from data_sync_etl.config import Settings
from data_sync_etl.adapters.ocr.engines import TesseractOCREngine
s = Settings()
TesseractOCREngine(s.tesseract_path, s.tesseract_languages, s.ocr_timeout_seconds).check_runtime()
print("PASS: Tesseract va ngon ngu da san sang")
PY
```

`OCR_ENGINE=native` chỉ đọc text có sẵn, không OCR ảnh/PDF scan. Không dùng `fake` cho dữ liệu thật.

## 5. Tải và cấu hình model embedding

Dùng [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), hỗ trợ đa ngôn ngữ, **384 chiều**, chạy CPU. Model dùng cho chỉ mục/tìm kiếm, không thay thế Tesseract.

### Tải một lần, lưu ngoài repository

Chạy trong Git Bash đã kích hoạt `.venv`, có Internet. Nếu đã có đúng model/revision thì bỏ qua bước tải.

```bash
unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE
python - <<'PY'
from pathlib import Path
from huggingface_hub import snapshot_download

model_id = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
revision = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
target = Path("D:/Models/paraphrase-multilingual-MiniLM-L12-v2")
snapshot_download(
    repo_id=model_id, revision=revision, local_dir=str(target),
    allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
    ignore_patterns=["onnx/*", "openvino/*"],
)
(target / "etl-model-revision.txt").write_text(revision + "\n", encoding="utf-8")
print("Da tai model:", target)
PY
```

Giữ đủ tokenizer, trọng số và các module như `1_Pooling`. Tải gián đoạn có thể chạy lại cùng lệnh. Không dùng chung thư mục với model khác.

### Khai báo trong `.env` và kiểm tra

```dotenv
INDEX_EMBEDDING_MODEL_PATH=D:/Models/paraphrase-multilingual-MiniLM-L12-v2
INDEX_EMBEDDING_MODEL_ID=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
INDEX_EMBEDDING_MODEL_VERSION=e8f8c211226b894fcb81acc59f3b34ba3efd5f42
INDEX_EMBEDDING_DIMENSION=384
```

Đường dẫn phải chứa `modules.json`; version phải khớp manifest `etl-model-revision.txt`. Không tự đổi version/dimension để bỏ kiểm tra. Biến cũ `VECTOR_DIMENSION` không quyết định dimension ContentBag.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python - <<'PY'
import math
from data_sync_etl.config import Settings
from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
s = Settings()
model = LocalSentenceEmbedding(
    s.index_embedding_model_path, s.index_embedding_model_id,
    s.index_embedding_model_version, s.index_embedding_dimension,
)
vector = model.embed("Học liệu tiếng Việt về phân số.")
assert len(vector) == s.index_embedding_dimension == 384
assert all(math.isfinite(value) for value in vector) and any(vector)
print("PASS: embedding tieng Viet 384 chieu")
PY
```

Backend/worker dùng model local, không tự tải model khi chạy. Lần nạp đầu có thể lâu hơn.

## 6. Google Drive — dành cho người mới pull code

**Pull code không mang theo quyền Drive, OAuth token hoặc cấu hình cá nhân.** Mỗi người tự đăng nhập bằng tài khoản có quyền đọc thư mục; không dùng token của người khác. Chỉ dùng Local thì bỏ qua mục này.

### 6.1. Chuẩn bị Google Cloud và thư mục Drive

1. Tạo/chọn project trong [Google Cloud Console](https://console.cloud.google.com/), bật **Google Drive API**.
2. Cấu hình **Google Auth platform**: Branding, Audience và Data Access. Với tài khoản cá nhân, dùng External; khi ứng dụng ở Testing, thêm email đăng nhập vào **Test users**.
3. Chỉ yêu cầu scope `https://www.googleapis.com/auth/drive.readonly`: đọc metadata và tải file, không sửa/xóa. Scope này có thể đọc các file tài khoản được phép truy cập; ETL giới hạn thêm bằng folder mapping, không phải quyền OAuth giới hạn riêng thư mục.
4. Trong **Clients**, tạo OAuth client loại **Desktop app**, tải JSON và lưu ngoài Git, ví dụ `D:/ETL_Secrets/desktop-client.json`.
5. Nhờ chủ thư mục Drive chia sẻ quyền **Reader** cho email của bạn. Nếu dùng OAuth project của nhóm, chủ project cũng cần thêm email vào Test users; chỉ chia sẻ client JSON qua kênh riêng được phép, không chia sẻ token.

Tham khảo [hướng dẫn OAuth Desktop của Google](https://developers.google.com/workspace/drive/api/quickstart/python). Không cần tạo API key hoặc bật chia sẻ công khai thư mục.

### 6.2. Cài extra và đăng nhập OAuth

Dừng API/worker trước khi cập nhật dependencies. Chạy từ repository, đã kích hoạt `.venv`:

```bash
python -X utf8 -m pip install -e ".[dev,embeddings,google-drive]"
mkdir -p /d/ETL_Secrets
python scripts/authorize_drive_readonly.py \
  --client-file "D:/ETL_Secrets/desktop-client.json" \
  --token-file "D:/ETL_Secrets/drive-authorized-user.json"
```

Tự đăng nhập và cấp quyền đọc trên trình duyệt. Chỉ tiếp tục khi terminal báo `OAUTH_SUCCESS`. Script tạo credential **authorized_user**, không in token và không ghi database. Đây là đăng nhập Google cho ETL, không phải đăng nhập WebApp.

Token đích phải là file **chưa tồn tại**, thư mục cha đã có và nằm ngoài repository. Nếu cần xác thực lại, chọn tên token mới rồi cập nhật đường dẫn; không ghi đè hoặc gửi nội dung JSON qua chat/Git/log.

### 6.3. Khai báo folder và kiểm tra chỉ đọc

Lấy `FOLDER_ID` từ link `https://drive.google.com/drive/folders/FOLDER_ID`, không lấy ID của một file. Sửa `.env`:

```dotenv
SOURCE_DRIVE_ENABLED=true
SOURCE_DRIVE_AUTH_TYPE=authorized_user
SOURCE_DRIVE_CREDENTIALS_FILE=D:/ETL_Secrets/drive-authorized-user.json
SOURCE_DRIVE_BUCKETS={"COURSE_DRIVE":"YOUR_FOLDER_ID"}
```

Ý nghĩa các biến Drive trong `.env`:

| Biến | Cách cấu hình |
| --- | --- |
| `SOURCE_DRIVE_ENABLED` | `true` để đọc Drive; `false` để tắt. |
| `SOURCE_DRIVE_AUTH_TYPE` | `authorized_user` khi đăng nhập OAuth cá nhân theo mục 6.2. |
| `SOURCE_DRIVE_CREDENTIALS_FILE` | Đường dẫn tuyệt đối tới token riêng trên máy, lưu ngoài repository. |
| `SOURCE_DRIVE_BUCKETS` | JSON ánh xạ bucket nguồn sang folder ID, ví dụ `{"COURSE_DRIVE":"YOUR_FOLDER_ID"}`. |

Drive hiện **chỉ là nguồn đọc**, chưa hỗ trợ lưu đầu ra lên Drive; file nhập vẫn lưu tại `STORAGE_ROOT`. Sau khi sửa `.env`, khởi động lại Backend và worker nhập file để nhận cấu hình mới.

`SOURCE_DRIVE_CREDENTIALS_FILE` trỏ tới **token vừa tạo**, không phải Desktop client JSON. Giữ nguyên `SOURCE_FILE_STORAGE_ROOT` và `STORAGE_ROOT`; Local vẫn hoạt động song song.

```bash
python - <<'PY'
from data_sync_etl.config import Settings
from data_sync_etl.adapters.storage.source_google_drive import SourceGoogleDriveFileReader
SourceGoogleDriveFileReader(Settings()).check()
print("PASS: truy cap thu muc Drive chi doc")
PY
```

Lệnh trên chỉ kiểm tra truy cập thư mục, chưa nhập file hoặc ghi PostgreSQL/MongoDB. API/worker cần chạy bằng tài khoản máy có quyền đọc token. Chỉ khởi động worker nhập file sau khi kiểm tra thành công.

### 6.4. Điều kiện để đồng bộ file Drive

Không tự quét folder rồi tạo học liệu. File phải có tham chiếu hợp lệ trong `resource_versions` của PostgreSQL nguồn:

| Trường nguồn | Giá trị |
| --- | --- |
| `storage_bucket` | `COURSE_DRIVE` — khớp key trong mapping |
| `storage_object_key` | `files/FILE_ID/ten-file-goc.pdf` — đúng tên và đuôi file |
| `source_hash` | SHA-256 thực tế của nội dung file |
| `lifecycle_status` | `APPROVED` theo cấu hình cho phép |

File phải nằm trong folder đã cấu hình và được phép tải. Dùng file PDF/Office/ảnh thực; Google Docs/Sheets/Slides native và shortcut chưa được hỗ trợ. ETL tải snapshot về `STORAGE_ROOT` rồi dùng pipeline hiện có.

Tham chiếu file và hash cần được xác nhận tại PostgreSQL nguồn theo bảng trên. Không đổi hàng loạt đường dẫn nguồn chỉ để thử Drive. Khi tắt Drive, đặt `SOURCE_DRIVE_ENABLED=false` nhưng **giữ bucket mapping** để không định tuyến nhầm sang Local.

Nếu dùng service account thay OAuth cá nhân: cấu hình `SOURCE_DRIVE_AUTH_TYPE=service_account`, trỏ tới credential service account ngoài Git và chia sẻ folder cho email service account với quyền Reader; không chạy script OAuth Desktop.

### 6.5. Đường dẫn Windows và Docker

Khi API/worker chạy trên Windows, dùng đường dẫn tuyệt đối: `SOURCE_FILE_STORAGE_ROOT=D:/SourceLearningFiles`, `STORAGE_ROOT=D:/ETLStorage`, token tại `D:/ETL_Secrets/drive-authorized-user.json`. Thư mục nguồn và đích phải riêng biệt; bucket Drive không thay đổi thư mục Local.

Compose hiện tại chỉ chạy database và migration, **không chạy API/worker hoặc tự mount thư mục nguồn/token**. Nếu tự đóng gói worker vào container, cần mount nguồn và token **read-only**, mount kho đích riêng có quyền ghi, rồi cấu hình đường dẫn bên trong container. Kết nối database trong mạng Compose dùng `postgres:5432`, `source-postgres:5432`, `mongo:27017`; `127.0.0.1` trong container không phải máy Windows. Không đưa token vào image hoặc Git.

### 6.6. Phân biệt trạng thái đồng bộ và xem tệp

Metadata đồng bộ thành công không có nghĩa file đã được nhập. Worker nhập file đọc Local/Drive, kiểm tra định dạng và SHA-256, lưu vào `STORAGE_ROOT`, rồi mới gửi yêu cầu xử lý. `CONTENT_READY` là đã chuẩn hóa; `INDEX READY` là đã có chỉ mục, không phải cùng một trạng thái.

Xem/tải tệp chỉ đọc bản đã nhập trong kho đích, không mở trực tiếp Local/Drive. Giao diện vẫn giữ kiểm tra quyền và chỉ mục hiện có: HTTP 409 nghĩa tệp chưa sẵn sàng, cần kiểm tra xử lý/chỉ mục; HTTP 404 nghĩa không tìm thấy tệp hoặc không có quyền. Học liệu nguồn chưa có phiên bản nhập sẽ hiển thị “Chưa nhập tệp từ nguồn”.

## 7. Khởi động hệ thống

### 7.1. Bật database trước

Mở Docker Desktop, vào thư mục gốc repository và chạy:

```bash
source .venv/Scripts/activate
docker compose up --build -d
docker compose ps -a
python -m data_sync_etl.adapters.mongo.schema --check
```

Chỉ tiếp tục khi database sẵn sàng, các bước migration/init thành công và schema PASS. Lần cài mới cần hoàn thành tài khoản reader, Tesseract và model ở các mục trên.

### 7.2. Nếu dùng Google Drive

Hoàn thành OAuth và cấu hình `.env` tại mục 6 trước khi chạy worker. Token còn hợp lệ thì **không cần đăng nhập Google lại mỗi lần start**.

Nếu chưa cài extra Drive, chạy một lần khi API/worker đang dừng:

```bash
python -X utf8 -m pip install -e ".[dev,embeddings,google-drive]"
```

Kiểm tra folder Drive bằng cấu hình hiện tại, không ghi database:

```bash
python -c "from data_sync_etl.config import Settings; from data_sync_etl.adapters.storage.source_google_drive import SourceGoogleDriveFileReader; SourceGoogleDriveFileReader(Settings()).check(); print('PASS: Drive read-only')"
```

Nếu chỉ dùng Local, bỏ qua bước này. Drive dùng **chung worker nhập file nguồn**, không có lệnh start Drive riêng và không cần worker thứ sáu. Kiểm tra Drive thất bại thì sửa credential/quyền/folder trước khi nhập file Drive.

### 7.3. Mở API, giao diện và các worker

Mở **5 terminal riêng** tại repository. Mỗi terminal Python cần chạy `source .venv/Scripts/activate` trước.

| Terminal | Lệnh |
| --- | --- |
| Backend | `python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| Frontend | `npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --strictPort` |
| Đồng bộ metadata | `etl run-master-sync-worker --worker-id master-sync-1` |
| Nhập file nguồn Local + Drive (nếu bật) | `etl run-source-file-worker --worker-id source-file-1` |
| OCR, chuẩn hóa, Index/Reindex | `etl run-ocr-worker --worker-id ocr-1` |

Mở [giao diện](http://127.0.0.1:3000), [Swagger](http://127.0.0.1:8000/docs), [Health](http://127.0.0.1:8000/health). Frontend proxy tới Backend **8000**. Compose không chạy thay các terminal này, không cài model/Tesseract trên máy.

Sau khi đổi `.env`, khởi động lại API và worker liên quan; không chỉ chờ `--reload`.

## 8. Kiểm tra chức năng

1. Nguồn PostgreSQL có dữ liệu: chạy `etl source-file-precheck` để kiểm tra cấu hình/nguồn (có thể tải file để xác minh, không phải lệnh đồng bộ). Sau đó chạy Full lần đầu từ giao diện hoặc `etl sync-master --stream all --full`.
2. Bật tự đồng bộ các nhóm cần dùng. Mặc định lịch metadata/đối soát file là 300 giây, không phải realtime. Nguồn cần đúng trạng thái cho phép, tham chiếu và hash.
3. Thêm học liệu trên giao diện hoặc chờ worker nhập file nguồn; đợi `CONTENT_READY`. Metadata đồng bộ thành công chưa có nghĩa OCR/Index đã hoàn tất.
4. Mở **Phân đoạn và chỉ mục**, chọn phiên bản, nhấn **Xây dựng chỉ mục** hoặc **Tái lập chỉ mục**. Đợi `PENDING → RUNNING → READY`, xác nhận ContentBag/vector sẵn sàng.
5. Sau khi schema, model và luồng thủ công PASS, có thể đặt `INDEX_AUTO_ENABLED=true`, khởi động lại API/OCR worker và thử học liệu mới. Không tự backfill toàn bộ dữ liệu cũ.

### Tìm kiếm và thực nghiệm trên Dev

Search công khai mặc định bị chặn khi chưa có bằng chứng quyền truy xuất; **PUBLISHED không đồng nghĩa được công khai toàn văn**. Chỉ khi đã xác nhận corpus thử nghiệm được phép sử dụng trên Dev local, bật:

```dotenv
APP_ENVIRONMENT=development
ADMIN_ENABLED=true
SEARCH_INTERNAL_DEV_ENABLED=true
```

Khởi động lại Backend trên loopback theo mục 7, không public proxy. Chế độ này không phải xác thực Production; vẫn chặn nội dung REJECTED/hạn chế và dữ liệu chưa đủ điều kiện READY.

- **Truy vấn:** thử BoW, Vector, Hybrid; mở nguồn tham chiếu và nội dung liên quan.
- **Thực nghiệm:** Dữ liệu đánh giá → Chạy thực nghiệm → Kết quả và báo cáo. Import JSON hoặc chấm nhãn `resource_id:grade` (0–3), không dùng điểm Search làm nhãn chuẩn.
- Chạy cùng dataset/k cho ba phương pháp; xem tiến độ %, Precision/Recall/MRR/nDCG và xuất CSV/JSON. Chưa có Ground Truth được con người xác nhận thì chưa nghiệm thu chất lượng tìm kiếm.
- Kết quả lưu dưới `STORAGE_ROOT/evaluations`. Giới hạn chạy `EVALUATION_RUN_TIMEOUT_SECONDS=300`; không tự tăng vượt giới hạn hoặc gửi lại POST khi chưa rõ trạng thái lần chạy cũ.

## 9. Những lần pull/chạy sau

Dừng API/worker trước khi cập nhật dependencies. Sau khi pull code vào worktree phù hợp:

```bash
source .venv/Scripts/activate
python -X utf8 -m pip install -e ".[dev,embeddings]"
# Nếu dùng Drive, dùng dòng này thay dòng pip ở trên:
# python -X utf8 -m pip install -e ".[dev,embeddings,google-drive]"
npm.cmd --prefix frontend ci
docker compose up --build -d
python -m data_sync_etl.adapters.mongo.schema --check
```

Đối chiếu biến mới trong `.env.example`, không chép đè `.env`. Giữ model/token hợp lệ; không cần tải/đăng nhập lại mỗi lần pull. Chạy lại 5 terminal theo mục 7.

Dừng từng tiến trình bằng **Ctrl+C**; dừng database và giữ dữ liệu bằng `docker compose stop`.

Kiểm tra mã nguồn:

```bash
python -m pytest tests/unit -q
python -m ruff check .
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
git diff --check
```

Integration tests cần fixture/database kiểm thử cô lập theo từng bài test và các biến `TEST_*` tương ứng; không trỏ chúng vào database Dev/Production chứa học liệu.

Các lệnh kiểm tra/vận hành bổ sung:

```bash
etl --help
etl sync-master --stream all --incremental
etl reconcile-master --stream all --dry-run
etl sync-mongo-metadata --stream all --full --dry-run
python -m data_sync_etl.adapters.mongo.schema --inventory
```

Metadata Master → MongoDB mặc định shared chỉ đối chiếu. `MASTER_MONGO_IDENTITY_PROJECTION=false` giữ nguyên chế độ này; identity projection cần bật riêng trên Development/Test với nguồn PostgreSQL, không tự tạo description/tags/bio. CLI mặc định dry-run; chỉ dùng `--apply` sau khi được phê duyệt ghi dữ liệu.

## 10. Lỗi thường gặp

Tài liệu kiến trúc/nghiệm thu: [SOURCE–Master–Mongo](docs/architecture/README.md),
[migration inventory](docs/architecture/migrations.md), [OAuth Drive chỉ đọc](docs/acceptance/drive-readonly.md),
[Ground Truth cần chấm thủ công](docs/acceptance/ground-truth.md), [báo cáo Prompt05](docs/audits/frontend-masterdata/report.md).
Hệ thống → Master Data chỉ đọc danh mục; provider/type là snapshot đã đồng bộ, không toàn bộ SOURCE.
Chi tiết học liệu chỉ bật Xem/Tải khi nội dung và chỉ mục sẵn sàng; API vẫn kiểm quyền/tệp đích.

| Hiện tượng | Cách kiểm tra |
| --- | --- |
| HTTP 503 / `BLOCKED_INDEX_SCHEMA` | Chạy `python -m data_sync_etl.adapters.mongo.schema --check`, xem log `mongo-init`. Schema cũ cần backup và phê duyệt sửa, không xóa volume hoặc bỏ guard. |
| Index/Vector báo thiếu runtime | Đúng `.venv`, extra embeddings, đường dẫn model, manifest/revision và 384 chiều; chạy kiểm tra mục 5. |
| OCR ảnh lỗi | Kiểm tra đường dẫn Tesseract, `eng`/`vie` và mục 4; khởi động lại worker sau khi sửa. |
| Worker không xử lý | Kiểm tra đúng worker đang chạy, cờ tự động, trạng thái nguồn và file/hash; xem log terminal. |
| Drive OAuth bị chặn | Đúng Desktop client, Drive API đã bật, email thuộc Test users và có quyền folder. Không dùng token của người khác. |
| Drive 403/404 hoặc token hết hiệu lực | Đúng tài khoản/folder mapping, file được phép tải; xác thực lại bằng script với tên token mới nếu token bị thu hồi/hết hiệu lực. |
| Drive `TOKEN_TARGET_NOT_NEW` | Token đích đã tồn tại; dùng token hợp lệ đang có hoặc chọn tên mới để xác thực lại. |
| Sai mật khẩu PostgreSQL | Đối chiếu role thực tế với URL; đổi `.env` không đổi mật khẩu role đã tồn tại. |
| Search rỗng / Thực nghiệm 403 | Kiểm tra quyền, local Dev và cấu hình mục 8; không tự mở dữ liệu thật cho công khai. |
| `etl.exe` bị khóa khi pip install | Ctrl+C API/worker đang dùng `.venv`, sau đó chạy lại pip; không cần xóa dữ liệu. |
| Giao diện không gọi được API | Kiểm tra Backend cổng 8000, Frontend cổng 3000 và Health; tránh chạy nhầm API cũ. |

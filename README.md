# ETL — Đồng bộ và xử lý học liệu

Đồng bộ PostgreSQL nguồn → PostgreSQL Master → nhập file → trích xuất/OCR → chuẩn hóa Content Units → ContentBag và vector embedding.

Hỗ trợ **PDF, DOCX, XLSX, TXT, CSV, PPTX, PNG, JPG, JPEG**. Không hỗ trợ MP3/MP4; chưa triển khai tìm kiếm hoặc đánh giá.

Hướng dẫn dưới đây dành cho **Development trên Windows, dùng Git Bash**: database chạy Docker; Backend, Frontend và worker chạy trên máy. Thực hiện lần lượt mục 1–6 khi cài mới; những lần sau dùng mục 7.

## 1. Chuẩn bị và cài dependencies

Cần Git, Python 3.12 trở lên, Node.js 22.12 trở lên và Docker Desktop có Docker Compose. Mở Docker Desktop trước khi chạy database.

Mở Git Bash tại thư mục gốc repository, nơi có `pyproject.toml` và `docker-compose.yml`:

```bash
python --version
node --version
docker compose version

python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,embeddings]"
npm.cmd --prefix frontend ci

test -f .env || cp .env.example .env
```

Nếu đã có `.venv`, chỉ kích hoạt và cập nhật dependencies; không cần tạo lại.
Extra `embeddings` cài runtime Sentence Transformers/PyTorch, **không tự tải model**.
Không ghi đè `.env` cũ hoặc commit mật khẩu/model vào Git.

Nếu dùng PowerShell, kích hoạt bằng `.\.venv\Scripts\Activate.ps1`. Các lệnh nhiều dòng `python - <<'PY'` bên dưới phải chạy trong **Git Bash**, không phải PowerShell.

## 2. Cấu hình `.env`

Mở `.env`, giữ các nhóm có sẵn và cập nhật những giá trị dưới đây. Thay toàn bộ `your-…-password` bằng mật khẩu riêng; đây không phải mật khẩu có sẵn trong hệ thống.

```dotenv
# API local; không thay thế cơ chế xác thực
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

# Kho file nguồn và kho ETL phải tách biệt
SOURCE_FILE_STORAGE_ROOT=D:/SourceLearningFiles
STORAGE_ROOT=D:/ETLStorage
SOURCE_FILE_ALLOWED_STATUSES=["APPROVED"]

# Một database MongoDB dùng chung
MONGO_URI=mongodb://127.0.0.1:27018
MONGO_DATABASE=etl_content
MONGO_METADATA_MODE=shared
SOURCE_MONGO_ENABLED=false
MASTER_MONGO_IDENTITY_BOOTSTRAP=false

# Chỉ bật auto sau khi kiểm tra schema và model thành công
INDEX_AUTO_ENABLED=false
INDEX_EMBEDDING_DIMENSION=384
```

Tạo hai thư mục file nếu chưa có:

```bash
mkdir -p /d/SourceLearningFiles /d/ETLStorage
```

File nguồn được đọc tại `<SOURCE_FILE_STORAGE_ROOT>/<storage_bucket>/<storage_object_key>`.
Hai kho không được trùng hoặc nằm bên trong nhau. Đổi đường dẫn mẫu nếu máy dùng ổ đĩa khác.

Lưu ý mật khẩu:

- `POSTGRES_PASSWORD` phải khớp mật khẩu trong `DATABASE_URL`; tương tự với tài khoản owner nguồn.
- Với cài mới theo Compose, nên dùng mật khẩu dài gồm chữ, số và dấu gạch ngang để tránh ký tự cần mã hóa trong URL. Mật khẩu có ký tự đặc biệt cần xử lý URL phù hợp cả ở cấu hình Compose.
- Database đã tồn tại: sửa `.env` **không đổi mật khẩu role PostgreSQL**. Dùng tài khoản/mật khẩu thực tế; không xóa volume để đặt lại.

## 3. Cài Tesseract OCR

Tesseract dùng cho ảnh và PDF scan. Đây là phần mềm cài riêng, không được cài bằng `pip`.

1. Mở [hướng dẫn Tesseract chính thức](https://tesseract-ocr.github.io/tessdoc/Installation.html), chọn bản Windows từ liên kết UB Mannheim.
2. Cài vào `C:\Program Files\Tesseract-OCR` hoặc ghi lại đường dẫn đã chọn.
3. Cài dữ liệu ngôn ngữ **English** và **Vietnamese**. Nếu thiếu, tải `eng.traineddata`, `vie.traineddata` từ [tessdata_fast chính thức](https://github.com/tesseract-ocr/tessdata_fast), chép vào thư mục `tessdata` của bản cài. Ghi vào Program Files có thể cần quyền quản trị.
4. Kiểm tra trong Git Bash:

```bash
"/c/Program Files/Tesseract-OCR/tesseract.exe" --version
"/c/Program Files/Tesseract-OCR/tesseract.exe" --list-langs
```

Danh sách phải có cả `eng` và `vie`. Thêm/cập nhật trong `.env`:

```dotenv
OCR_ENGINE=tesseract
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSERACT_LANGUAGES=vie+eng
OCR_TIMEOUT_SECONDS=60
```

Kiểm tra đúng cấu hình mà Backend/worker sẽ dùng:

```bash
python - <<'PY'
from data_sync_etl.config import Settings
from data_sync_etl.adapters.ocr.engines import TesseractOCREngine

s = Settings()
TesseractOCREngine(
    s.tesseract_path, s.tesseract_languages, s.ocr_timeout_seconds
).check_runtime()
print("PASS: Tesseract và dữ liệu ngôn ngữ đã sẵn sàng")
PY
```

Nếu chỉ dùng `OCR_ENGINE=native`, hệ thống đọc text có sẵn nhưng không đáp ứng OCR ảnh/PDF scan. Không dùng `fake` cho dữ liệu thật.

## 4. Tải model embedding local

Model dùng: [sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), vector **384 chiều**, chạy CPU trong adapter hiện tại. Model embedding phục vụ chỉ mục, không thay thế Tesseract OCR.

### 4.1. Tải model một lần

Cần Internet ở bước tải. Chọn thư mục ngoài repository, không dùng chung với một model khác. Nếu đã có đầy đủ đúng model/revision, bỏ qua bước tải.

Chạy trong Git Bash đã kích hoạt `.venv`:

```bash
unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE
python - <<'PY'
from pathlib import Path
from huggingface_hub import snapshot_download

model_id = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
revision = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
target = Path("D:/Models/paraphrase-multilingual-MiniLM-L12-v2")

snapshot_download(
    repo_id=model_id,
    revision=revision,
    local_dir=str(target),
    allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"],
    ignore_patterns=["onnx/*", "openvino/*"],
)
(target / "etl-model-revision.txt").write_text(revision + "\n", encoding="utf-8")
print("Đã tải model:", target)
PY
```

Giữ nguyên các file cấu hình/tokenizer, trọng số `.safetensors` và thư mục module như `1_Pooling`. Không chỉ tải riêng file trọng số.
Nếu tải gián đoạn, chạy lại cùng lệnh để tiếp tục. Cách tải tham khảo: [Hugging Face Hub](https://huggingface.co/docs/huggingface_hub/guides/download).

### 4.2. Khai báo model trong `.env`

```dotenv
INDEX_EMBEDDING_MODEL_PATH=D:/Models/paraphrase-multilingual-MiniLM-L12-v2
INDEX_EMBEDDING_MODEL_ID=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
INDEX_EMBEDDING_MODEL_VERSION=e8f8c211226b894fcb81acc59f3b34ba3efd5f42
INDEX_EMBEDDING_DIMENSION=384
```

`INDEX_EMBEDDING_MODEL_PATH` phải trỏ đến thư mục chứa `modules.json`.
`INDEX_EMBEDDING_MODEL_VERSION` phải khớp nội dung `etl-model-revision.txt` do bước tải tạo ra.
Không sửa số chiều hoặc chỉ ghi một revision tùy ý để bỏ qua kiểm tra.
Biến legacy `VECTOR_DIMENSION` không quyết định số chiều ContentBag.

### 4.3. Kiểm tra embedding tiếng Việt, không dùng mạng

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python - <<'PY'
import math
from data_sync_etl.config import Settings
from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding

s = Settings()
model = LocalSentenceEmbedding(
    s.index_embedding_model_path,
    s.index_embedding_model_id,
    s.index_embedding_model_version,
    s.index_embedding_dimension,
)
vector = model.embed("Học liệu tiếng Việt về phân số và phép tính.")
assert len(vector) == s.index_embedding_dimension == 384
assert all(math.isfinite(value) for value in vector)
assert any(value != 0 for value in vector)
print("PASS: embedding tiếng Việt có 384 chiều")
PY
```

Lần nạp đầu có thể lâu hơn. Backend không tự tải model; worker sử dụng model local. Không cần GPU theo cấu hình này.

## 5. Khởi tạo database

Các lệnh mục này dành cho database **Dev đã xác nhận đúng đích**; Compose có chạy migration PostgreSQL. Với database đang có dữ liệu, sao lưu và duyệt thay đổi trước.

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
python -m data_sync_etl.adapters.mongo.schema --check
```

Kết quả cần có:

- PostgreSQL Master `127.0.0.1:5432/etl`, PostgreSQL nguồn `127.0.0.1:5433/etl_source`, MongoDB `127.0.0.1:27018/etl_content`.
- `master-migrate`, `source-migrate`, `mongo-init` kết thúc với mã **0**.
- Schema MongoDB `schema_ready=true`, đủ 5 collection và 6 index nghiệp vụ; ContentBag 384 chiều.

Năm collection: `users`, `learning_resources`, `ocr_pages`, `mongo_content_units`, `content_bags`. Chunk preview không lưu thành collection riêng. Metadata `users`/`learning_resources` được đối chiếu tại chỗ, không tự sao chép toàn bộ bản ghi Master sang MongoDB.

**Database MongoDB mới trống:** `mongo-init` tự tạo collection, validator và index.
**Database cũ thiếu/sai schema:** initializer dừng, không tự sửa dữ liệu. Dùng `--inventory` và `--check` để chẩn đoán; cần backup, kiểm chứng restore và phê duyệt riêng trước khi dùng `--apply` hoặc migration tương ứng. Không dùng `docker compose down -v` để chữa lỗi schema.

### Tạo tài khoản chỉ đọc nguồn — cài mới một lần

Sau khi `source-migrate` thành công, với tên database/user mặc định ở mục 2:

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong màn hình `psql`, đặt mật khẩu reader rồi thoát:

```text
\password etl_source_reader
\q
```

Nhập mật khẩu hai lần theo yêu cầu, dùng đúng giá trị đã khai báo trong `SOURCE_DATABASE_URL`.
Nếu thay tên database, phải đối chiếu cả câu `GRANT CONNECT` trong script trước khi chạy.
Database mới chưa có dữ liệu nghiệp vụ; bước khởi tạo không tự nạp học liệu mẫu.

## 6. Chạy Backend, Frontend và worker

Mở **5 terminal riêng** tại thư mục gốc repository. Trong mỗi terminal Python, chạy `source .venv/Scripts/activate` trước. Giữ các terminal hoạt động trong lúc sử dụng.

| Terminal | Lệnh | Vai trò |
| --- | --- | --- |
| Backend | `python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers` | API |
| Frontend | `npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --strictPort` | Giao diện cổng 3000 |
| Master Sync | `etl run-master-sync-worker --worker-id master-sync-1` | Đồng bộ metadata theo lịch/cấu hình bật tắt |
| Nhập file | `etl run-source-file-worker --worker-id source-file-1` | Nhập file nguồn, đối soát và retry |
| OCR/Index | `etl run-ocr-worker --worker-id ocr-1` | Trích xuất, chuẩn hóa và xử lý hàng đợi Index/Reindex |

Docker Compose hiện **không chạy thay** 5 tiến trình trên và không cài Tesseract/model cho máy host.

Mở [giao diện](http://127.0.0.1:3000), [Swagger API](http://127.0.0.1:8000/docs), [Health](http://127.0.0.1:8000/health).
Frontend đang proxy về cổng **8000**; không đổi riêng cổng Backend rồi tiếp tục dùng giao diện như cũ.

### Kiểm tra luồng sử dụng

1. Nếu dùng nguồn PostgreSQL, kiểm tra kho file và chạy Full lần đầu trong giao diện Đồng bộ dữ liệu nguồn hoặc terminal riêng:
   ```bash
   etl source-file-precheck
   etl sync-master --stream all --full
   ```
2. Bật tự đồng bộ từng nhóm theo nhu cầu. Mặc định lịch đồng bộ là 300 giây, không phải realtime. File nguồn cần trạng thái được phép, mặc định `APPROVED`, cùng đường dẫn/hash hợp lệ.
3. Thêm học liệu trên giao diện hoặc chờ worker nhập file nguồn. Đợi xử lý đạt `CONTENT_READY`.
4. Trong **Phân đoạn và chỉ mục**, chọn phiên bản, xem phân đoạn và nhấn **Xây dựng chỉ mục**. Khi cần tính lại dùng **Tái lập chỉ mục**; không phải chạy lại OCR.
5. Đợi `PENDING → RUNNING → READY`; kiểm tra ContentBag và vector sẵn sàng. Đồng bộ metadata thành công không đồng nghĩa OCR hay Index đã hoàn tất.
6. Sau khi schema/model và luồng thủ công đã PASS, đổi `INDEX_AUTO_ENABLED=true`, khởi động lại Backend và OCR worker. Thử bằng học liệu mới; không tự backfill hàng loạt phiên bản cũ.

## 7. Các lần chạy sau và kiểm thử

Bật Docker Desktop, mở terminal tại repository rồi chạy:

```bash
source .venv/Scripts/activate
docker compose up --build -d
python -m data_sync_etl.adapters.mongo.schema --check
```

Sau khi kiểm tra PASS, mở lại 5 terminal theo mục 6. Khi cập nhật dependencies, chạy lại `pip install -e ".[dev,embeddings]"` và `npm.cmd --prefix frontend ci`. Không cần tải lại model nếu revision không đổi.

Dừng API/Frontend/worker bằng **Ctrl+C** trong từng terminal. Dừng database nhưng giữ dữ liệu:

```bash
docker compose stop
```

Lệnh kiểm tra mã nguồn, chạy từ thư mục gốc:

```bash
python -m pytest tests/unit -q
python -m ruff check .
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run lint
git diff --check
```

Integration tests chỉ chạy khi đã cấu hình các biến `TEST_*` trỏ đến database kiểm thử cô lập theo fixture; không dùng database Dev/Production đang có học liệu.

Một số lệnh vận hành:

```bash
etl --help
etl sync-master --stream all --incremental
etl reconcile-master --stream all --dry-run
python -m data_sync_etl.adapters.mongo.schema --inventory
```

## 8. Lỗi thường gặp

| Hiện tượng | Cần kiểm tra |
| --- | --- |
| `BLOCKED_INDEX_SCHEMA` / schema chưa hợp lệ | Chạy `schema --check`, xem `docker compose logs mongo-init`. Database cũ cần kế hoạch sửa schema được duyệt; không bỏ guard hoặc xóa dữ liệu. |
| `BLOCKED_EMBEDDING_RUNTIME` / HTTP 503 khi Index | Đúng `.venv`, đã cài extra embeddings, đủ file model và manifest khớp revision; chạy lại kiểm tra mục 4.3. |
| `INVALID_EMBEDDING` | Model, cấu hình và schema ContentBag phải cùng 384 chiều. |
| Thiếu Tesseract / thiếu ngôn ngữ | Kiểm tra đường dẫn, `--list-langs` và mục 3; cài `pip` không cung cấp Tesseract binary. |
| Không thấy tự đồng bộ | Worker đang chạy, nhóm đã bật, đến lịch chạy; có baseline Full nếu Incremental yêu cầu. |
| Trạng thái nguồn chưa được phép nhập | Kiểm tra lifecycle của phiên bản nguồn và `SOURCE_FILE_ALLOWED_STATUSES`; retry không tự phê duyệt tài liệu Draft. |
| Health 503 / không kết nối database | Kiểm tra Docker, cổng, tài khoản và mật khẩu thực tế; sửa `.env` không đổi mật khẩu trong volume cũ. |
| API trả 400/422 | Xem response trong Network/Swagger và log Backend; không thể kết luận nguyên nhân chỉ từ dòng access log. |
| Cổng 8000/3000 đang dùng | Dừng đúng Backend/Frontend Dev cũ bằng Ctrl+C, không kill tiến trình chưa xác định. |

Sau khi sửa `.env`, khởi động lại Backend và các worker liên quan để nạp cấu hình mới.

## 9. Tra cứu học liệu

Mở **Tra cứu → Tìm kiếm học liệu**, nhập truy vấn, chọn BoW/Vector/Hybrid và bộ lọc. Trang Phương pháp truy vấn cho phép chọn chế độ; trang Kết quả giữ tiêu chí trong URL, có phân trang và thử lại.

- `POST /api/v1/search`: JSON gồm `query`, `method`, `filters`, `page`, `size`. Xem hợp đồng đầy đủ tại Swagger.
- BoW: cosine trên tập keyword nhị phân, không phải TF-IDF/BM25. Vector: cosine với vector ContentBag cùng model/revision. Hybrid: RRF với k=60.
- Mỗi kết quả là một học liệu, lấy Bag khớp tốt nhất. Mặc định dùng version_number lớn nhất trong các lifecycle `INGESTED`, `APPROVED`, `PUBLISHED`, sau đó bắt buộc CONTENT_READY + Index READY; không tự lùi về phiên bản cũ nếu bản mới chưa READY. Filter phiên bản cụ thể được kiểm tra riêng.
- Chỉ đọc học liệu active/PUBLISHED và leaf có `retrieval_eligible=true`; không tự phê duyệt, OCR hay lập chỉ mục từ Search. Content Units mới mặc định chưa có quyền truy xuất.
- `SEARCH_MAX_BAGS=2000`, `SEARCH_TIMEOUT_SECONDS=15` là mặc định. Vượt giới hạn phải thu hẹp bộ lọc; không lấy một phần kho rồi nhận là kết quả toàn kho. Vector/Hybrid cần model local hợp lệ, không tự chuyển sang BoW khi model lỗi.

Kiểm thử Search cô lập, không dùng database Dev:

```bash
python -m pytest tests/unit/test_search.py tests/integration/test_search_integration.py -q
npm.cmd --prefix frontend run build
node --experimental-strip-types --test frontend/tests/searchApi.test.mjs
node --test frontend/tests/searchUi.test.mjs
```

Test browser dùng Edge headless và HTTP fixture cục bộ. Integration test dùng SQLAlchemy/SQLite cùng Mongo/model doubles đúng contract; không thay thế nghiệm thu model thật và database thực tế.

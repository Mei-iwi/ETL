# ETL — Đồng bộ và xử lý học liệu

Đồng bộ PostgreSQL nguồn → Master Data và Mongo metadata nguồn → Mongo đích;
nhập file local, trích xuất/OCR, chuẩn hóa và lưu Content Units. Frontend React hỗ trợ
quản lý, đồng bộ và retry. Giai đoạn hiện tại dừng ở OCR/Content Units, chưa chạy
chunking mới, keywords, BoW/content_bags, embeddings, Search hoặc đánh giá.

Master Sync chỉ đồng bộ metadata; nội dung hoàn tất khi đạt `CONTENT_READY`.
`COMPLETED` trên API/giao diện đồng bộ là PostgreSQL Master, không xác nhận Mongo
metadata thành công. `CONTENT_READY` không đồng nghĩa đã lập chỉ mục tìm kiếm.
Hỗ trợ **PDF, DOCX, XLSX, TXT, CSV, PPTX, PNG, JPG, JPEG**; không tiếp nhận/xử lý MP3/MP4.

## 1. Cài đặt

Cần Python ≥ 3.12, Node.js 20.x (≥ 20.19) hoặc ≥ 22.12, Docker Desktop/Compose.
Các lệnh dùng **Git Bash trên Windows**, bắt đầu tại thư mục gốc dự án.

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e '.[dev]'
test -f .env || cp .env.example .env
cd frontend
npm.cmd install
cd ..
```

Linux/macOS dùng `source .venv/bin/activate` và `npm`. PowerShell dùng
`./.venv/Scripts/Activate.ps1`, hoặc gọi trực tiếp `./.venv/Scripts/python.exe`
và `./.venv/Scripts/etl.exe` nếu không activate được.

## 2. Cấu hình .env

Dùng [.env.example](.env.example) làm mẫu; **không chép đè .env đang dùng hoặc commit mật khẩu**.

| Cấu hình | Vai trò |
| --- | --- |
| `POSTGRES_PASSWORD`, `DATABASE_URL` | Mật khẩu Docker Master và URL backend phải khớp |
| `SOURCE_POSTGRES_PASSWORD`, `SOURCE_MIGRATION_DATABASE_URL` | Owner database nguồn, dùng migration |
| `SOURCE_DATABASE_URL` | Reader nguồn chỉ có SELECT, mật khẩu riêng |
| `SOURCE_ADAPTER=postgresql` | Dùng PostgreSQL nguồn thật; mẫu mặc định là `json` |
| `SOURCE_FILE_STORAGE_ROOT` | Kho nguồn tuyệt đối, có thật và đọc được |
| `STORAGE_ROOT` | Kho đích có quyền ghi, không trùng/lồng với kho nguồn |
| `ADMIN_ENABLED=true` | Bật quản trị giao diện local, không thay thế xác thực |
| `MONGO_URI`, `MONGO_DATABASE` | Host mặc định `mongodb://127.0.0.1:27018`, DB `etl_content` |

Ví dụ cho local dùng PostgreSQL nguồn:

```dotenv
SOURCE_ADAPTER=postgresql
SOURCE_FILE_STORAGE_ROOT=D:/SourceLearningFiles
STORAGE_ROOT=D:/ETLStorage
ADMIN_ENABLED=true
```

Đổi đường dẫn theo máy. File nguồn nằm tại `<root>/<storage_bucket>/<storage_object_key>`;
bucket/key lấy từ PostgreSQL. Mặc định chỉ nhập phiên bản nguồn `APPROVED`.
Đổi mật khẩu trong .env **không tự đổi** mật khẩu PostgreSQL trong volume đã tồn tại.

### Tesseract OCR — cần cho ảnh và PDF scan

PDF có text và DOCX/XLSX/TXT/CSV/PPTX không cần Tesseract.
Pip và Compose không cài Tesseract trên Windows.

1. Cài Tesseract từ [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki),
   được [tài liệu Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html#windows)
   giới thiệu; chọn English (`eng`) và Vietnamese (`vie`).
2. Kiểm tra trong **PowerShell**, sửa đường dẫn nếu cài ở vị trí khác:

   ```powershell
   & 'C:\Program Files\Tesseract-OCR\tesseract.exe' --version
   & 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
   ```

   Nếu thiếu ngôn ngữ, tải `eng.traineddata` / `vie.traineddata` từ
   [tessdata_fast chính thức](https://github.com/tesseract-ocr/tessdata_fast)
   và đặt vào thư mục `Tesseract-OCR\tessdata` (có thể cần quyền admin).
3. Sửa các dòng tương ứng trong .env, không khai báo trùng:

   ```dotenv
   OCR_ENGINE=tesseract
   TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
   TESSERACT_LANGUAGES=vie+eng
   ```

4. Restart backend và OCR worker, thử ảnh có chữ rõ. Job đã lỗi dùng **Xử lý lại**;
   chưa có job dùng **Bắt đầu xử lý**. Adapter tự dùng/dọn ảnh tạm tên ASCII,
   không cần đổi tên thư mục tiếng Việt.

Ubuntu: `sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie`,
kiểm tra `tesseract --list-langs`, đặt `TESSERACT_PATH=tesseract`.
`OCR_ENGINE=native` chỉ đọc text PDF; `fake` dành cho demo/test.

## 3. Database và migration

Bật Docker Desktop, kiểm tra mật khẩu/URL trước khi chạy; sao lưu DB đang có dữ liệu:

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate
```

Compose chạy PostgreSQL Master (5432), PostgreSQL nguồn (5433), MongoDB (27018).
Hai service migration phải kết thúc `Exited (0)`; không tự seed dữ liệu nguồn
hoặc chạy backend/worker. Nếu đổi cổng trong .env, cập nhật URL tương ứng.

Migration trên host (khi cập nhật code hoặc kiểm tra thủ công):

```bash
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
python -m alembic -c alembic_source.ini upgrade head
python -m alembic -c alembic_source.ini current
```

Master và Source là hai DB riêng. Head Master hiện tại `0014`: `0013` thêm reference
users/resources; `0014` thêm `grade_mongo_id`, reference version/unit theo ảnh Master.
Không backfill lịch sử; chưa migrate có thể gây API 500 do thiếu cột.

### Reader nguồn — thiết lập lần đầu

Lệnh dùng DB/owner mặc định trong .env.example; sửa nếu bạn dùng tên khác:

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong psql, đặt mật khẩu rồi thoát:

```text
\password etl_source_reader
\q
```

Cập nhật mật khẩu vào `SOURCE_DATABASE_URL`; không dùng owner làm reader.
Script cấp SELECT cả `resource_versions` và kiểm tra reader không có quyền ghi.

### MongoDB

```bash
python -m data_sync_etl.adapters.mongo.schema --check
```

Với DB mới, tạo collections/validators/indexes:

```bash
python -m data_sync_etl.adapters.mongo.schema --apply
```

DB đã có dữ liệu: đọc kết quả check, xử lý sai khác trước khi apply.

## 4. Chạy ứng dụng

Mỗi thành phần chạy trong **một terminal riêng**, bắt đầu tại thư mục gốc.
Terminal Python cần `source .venv/Scripts/activate` trước khi chạy.

| Thành phần | Lệnh |
| --- | --- |
| Backend | `python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| Frontend | `cd frontend` rồi `npm.cmd run dev -- --host 127.0.0.1 --strictPort` |
| Master Sync worker | `etl run-master-sync-worker --worker-id master-sync-1` |
| File import worker | `etl run-source-file-worker --worker-id source-file-1` |
| OCR/ETL worker | `etl run-ocr-worker --worker-id ocr-1` |

Frontend: http://127.0.0.1:3000 · Swagger: http://127.0.0.1:8000/docs ·
Health: http://127.0.0.1:8000/health. Vite proxy `/api`, `/admin`, `/health` đến backend 8000.

Master worker đồng bộ metadata theo lịch; file worker nhập phiên bản; OCR worker xử lý
nội dung và hoàn tất projection Mongo. Backend HTTP không thay thế worker.
Thêm `--once` vào lệnh worker để chạy một lượt; im lặng khi chưa có việc là bình thường.

## 5. Đồng bộ và sử dụng

Kiểm tra cấu hình và chạy Full baseline lần đầu:

```bash
etl source-file-precheck
etl sync-master --stream all --full
```

Trên giao diện **Kho học liệu**:

- **Đồng bộ dữ liệu nguồn**: chọn Full/Incremental, bật từng nhóm/tất cả.
  Auto cần reader hợp lệ, PostgreSQL nguồn/Master, Master worker và Full baseline thành công.
  Chu kỳ mặc định 300 giây (`MASTER_SYNC_AUTO_INTERVAL_SECONDS`), không phải realtime tức thì.
- **Lỗi đồng bộ**: tab **Đồng bộ lại** chứa tệp retry được; tab **Lỗi đồng bộ** chứa tệp
  không thể retry. Phân trang 7 tệp; retry chỉ xếp hàng, worker thực hiện sau đó.
- **Thêm học liệu** hoặc cập nhật file trong chi tiết: chuyển đến trang xử lý,
  chờ `CONTENT_READY`, mở nội dung trích xuất khi cần. Không phải upload lại file đồng bộ nguồn.

XLSX đọc giá trị cache, không tính công thức; TXT/CSV dùng UTF-8/BOM;
PPTX chỉ trích xuất text/bảng, không OCR ảnh nhúng. Tắt Auto không dừng job đã xếp hàng.
Precheck thành công không chứng minh ETL hoàn tất.

### Metadata Mongo nguồn → Mongo đích

Để đồng bộ metadata mở rộng **thật**, dùng Mongo nguồn có hai collection `users`,
`learning_resources`. Có thể dùng chung server hiện tại nhưng phải tách database;
không seed metadata mẫu vào nguồn thật. Chỉ sửa .env sau khi operator đã phê duyệt
database nguồn thực, quyền read và phạm vi ghi đích; mặc định nguồn Mongo tắt:

```dotenv
SOURCE_MONGO_ENABLED=true
SOURCE_MONGO_DATABASE=source_metadata
# SOURCE_MONGO_URI=mongodb://127.0.0.1:27018
```

URI không khai báo sẽ dùng `MONGO_URI`. Tài khoản Mongo nguồn chỉ cần quyền read.
Nguồn/đích cùng database và server bị chặn, kể cả alias hoặc node cùng replica set.
`source_metadata` là ví dụ; thay bằng database nguồn thực có sẵn. PostgreSQL nguồn
phải khai báo `mongo_id` đúng String _id của document nguồn, có thể khác PostgreSQL id.

```bash
etl sync-master --stream all --full
etl sync-mongo-metadata --stream all --full
```

Hook metadata chạy sau PG commit; lệnh thứ hai chạy bù hoặc nhận thay đổi chỉ có ở
Mongo, không cần sync PG lại. Hiện dùng **FULL_RECONCILE theo batch**, không có
incremental Mongo/CDC hoặc checkpoint timestamp bền vững. Checkpoint trả về chỉ là
tiến độ lượt quét; retry quét lại idempotent.

Source NULL/absent mapping được `skipped_unmapped`, **không fallback ID**. Field
metadata vắng không xóa field đích; explicit NULL cập nhật NULL. Created_at đích giữ
nguyên, payload lặp không đổi timestamp; doc metadata đích mới hơn không bị ghi đè.
Inactive/deleted/ARCHIVED bị bỏ qua, DRAFT vẫn mirror metadata và không được xem là
public. Không đồng bộ ngược hoặc tạo Master cho document Mongo orphan.

Mongo lỗi không rollback Master; xem log `mongo_source_metadata_sync` và chạy bù.
SOURCE_MONGO_ENABLED bật sẽ ưu tiên nguồn thật, không chạy identity bootstrap song song.
Chi tiết: [nguồn kép và ETL](docs/dual_source_etl.md).

#### Chế độ legacy khi chưa bật Mongo nguồn

Mặc định bootstrap tắt: `PARTIAL_SOURCE_FIELDS` / `SKIPPED_NO_EXTENSION` là có chủ đích
vì chưa có nguồn bio/description/authors/tags; `--full` không tự bật bootstrap.

Nếu đã xác nhận DEV/TEST hoặc chính sách triển khai cho phép tạo identity,
sửa .env gốc sau khi migrate:

```dotenv
MASTER_MONGO_IDENTITY_BOOTSTRAP=true
MASTER_MONGO_IDENTITY_SCOPE=source_verified
MASTER_MONGO_IDENTITY_IDS={}
```

Chỉ chọn bản ghi tồn tại trong PostgreSQL nguồn. Dùng `Master.mongo_id` làm Mongo `_id`;
mapping NULL được fallback `Master.id` và lưu tại Master. Không ghi vào nguồn,
không tự đổi mapping A→B hoặc ghi đè document Mongo đã có.
Phạm vi mặc định `approved_ids` chỉ chọn ID PostgreSQL đã duyệt trong allowlist.

```bash
etl project-master-mongo --stream all --full
```

Sync Master cũng gọi projection; chạy lại có thể báo `unchanged`.
`READY_IDENTITY_ONLY` chỉ xác nhận document ID/timestamps, không phải metadata đầy đủ.
Mongo lỗi không rollback Master; dùng lệnh trên chạy bù. Tắt bootstrap và restart để ngừng
tạo identity. Grade chỉ giữ reference, không tạo collection mới; content unit lưu
reference theo ID đích sau projection thành công. Version reference vẫn NULL vì chưa
có collection đích. Chi tiết: [nguồn kép và vận hành](docs/dual_source_etl.md).

### CLI thường dùng

```bash
etl --help
etl sync-master --stream resources --incremental
etl ingest-resource --resource-id RESOURCE_ID --bucket BUCKET --object-key FILE_KEY
etl create-ocr-job --resource-version-id VERSION_ID
etl etl-status --resource-version-id VERSION_ID
etl resume-etl --resource-version-id VERSION_ID
etl postprocess --resource-version-id VERSION_ID
etl recover-stale-ocr-tasks
```

Thay ID/bucket/key bằng giá trị thực trong kho đích; không chạy postprocess trước khi OCR hoàn tất.
Demo trên **DB riêng**: đặt `SOURCE_ADAPTER=json`, tắt identity bootstrap, rồi chạy
`etl demo-run --fixture-resource-id resource-1`. JSON không hỗ trợ Auto/nhập file ERD.

## 6. Cập nhật sau khi pull

Giữ .env hiện tại, đối chiếu biến mới trong .env.example, rồi chạy:

```bash
python -m pip install -e '.[dev]'
docker compose up --build -d
docker compose logs master-migrate source-migrate
cd frontend
npm.cmd install
cd ..
```

Nếu dùng DB ngoài Compose, chạy migration host ở mục 3. Sau migration thành công,
restart backend và **tất cả worker**; `--reload` chỉ áp dụng cho backend.
CLI đọc .env mỗi lần chạy, không cần mở lại terminal.

## 7. Kiểm thử

Backend tại thư mục gốc:

```bash
python -m pytest -q -p no:cacheprovider
python -m ruff check src tests alembic alembic_source scripts
```

Integration cần **DB test riêng**, không trỏ vào DB đang dùng. Tạo hai DB test trước,
thay tài khoản/mật khẩu trong ví dụ và export trong terminal Git Bash:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://etl:<password>@127.0.0.1:5432/etl_test'
export TEST_SOURCE_DATABASE_URL='postgresql+psycopg://etl_source_admin:<password>@127.0.0.1:5433/etl_source_test'
export TEST_MONGO_URI='mongodb://127.0.0.1:27018'
python -m pytest tests/integration -q -p no:cacheprovider
```

Mật khẩu có ký tự đặc biệt cần URL-encode. Chỉ thêm biến vào .env không đủ cho fixture
đọc `os.environ`. Thiếu cấu hình/runtime sẽ SKIP, không phải integration PASS.

Frontend:

```bash
cd frontend
npm.cmd run build
npm.cmd run lint
```

Quy trình test cô lập, nghiệm thu và giới hạn: [runbook](docs/dual_source_etl.md).
QA không fallback sang database ứng dụng; tên database test phải chứa thành phần
`test` riêng (ví dụ `etl_test`, `etl_source_test`). Không dùng `docker compose up`
để pre-flight read-only vì service migration có thể ghi database.

## 8. Lỗi thường gặp và dừng hệ thống

| Hiện tượng | Cách xử lý |
| --- | --- |
| API 500 sau pull, báo thiếu bảng/cột | Kiểm tra traceback, đúng URL Master và migration head; không phải mọi lỗi 500 đều do schema |
| 503 khi đọc nguồn | Kiểm tra DB/reader, SELECT trên resource_versions, kho file và thông báo lỗi cụ thể |
| Auto không chạy | Kiểm tra baseline, công tắc, Master worker và chu kỳ; restart sau đổi config |
| PARTIAL_SOURCE_FIELDS, Mongo metadata rỗng | Kiểm tra bootstrap/scope ở .env gốc; xem mục 5 |
| BLOCKED_MONGO_ID_MAPPING | Đối soát mapping/duplicate/ObjectId; không tự đổi ID hay xóa document |
| OCR_PENDING kéo dài | Kiểm tra OCR worker; chỉ chạy API chưa đủ |
| BLOCKED_OCR_RUNTIME / OCR_FAILED | Kiểm tra Tesseract path, vie+eng, worker log; restart rồi retry |
| File nhập xong nhưng chưa CONTENT_READY | Kiểm tra OCR/finalization/MongoDB và etl-status |
| 422 | Đọc response validation, kiểm tra payload và phiên bản backend; không mặc định lỗi kết nối |

Dừng backend/frontend/worker bằng `Ctrl + C`, dừng DB bằng:

```bash
docker compose stop
```

Chạy lại DB bằng `docker compose up --build -d`, rồi chạy các terminal ở mục 4.
**Không dùng docker compose down -v để sửa lỗi: lệnh này xóa volume dữ liệu.**

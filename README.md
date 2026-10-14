# Công cụ đồng bộ và xử lý học liệu — ETL

Ứng dụng đồng bộ metadata từ PostgreSQL nguồn sang Master Data, nhập học liệu từ kho file local,
trích xuất nội dung/OCR, chuẩn hóa và lưu kết quả vào PostgreSQL, MongoDB.
Giao diện React hỗ trợ quản lý học liệu, bật/tắt đồng bộ và thử lại các tệp lỗi.

Luồng chính: **Nguồn ERD → Master Data → nhập file → trích xuất/chuẩn hóa → PostgreSQL + MongoDB**.
Master Sync thành công chỉ xác nhận metadata; nội dung hoàn tất khi đạt `CONTENT_READY`.

## 1. Chuẩn bị và cài đặt

Cần Python 3.12 trở lên, Node.js 20.19.x hoặc từ 22.12 trở lên, Docker Desktop và Docker Compose.
Tesseract OCR cần cài riêng nếu muốn xử lý ảnh hoặc PDF scan; `pip install` không cài phần mềm này.
Các lệnh dưới đây dành cho **Git Bash trên Windows**, bắt đầu tại thư mục gốc dự án.

Cài backend và tạo cấu hình nếu chưa có:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e '.[dev]'
test -f .env || cp .env.example .env
```

Cài frontend:

```bash
cd frontend
npm.cmd install
cd ..
```

Linux/macOS dùng `source .venv/bin/activate` và `npm` thay `npm.cmd`.
PowerShell có thể gọi trực tiếp `./.venv/Scripts/python.exe` và
`./.venv/Scripts/etl.exe` thay `python` và `etl`, không cần activate.

## 2. Cấu hình .env

[.env.example](.env.example) đã chia nhóm và giải thích vai trò từng biến.
Sửa `.env` theo môi trường của bạn; giữ file này local, không commit.

| Nhóm | Cấu hình cần kiểm tra |
| --- | --- |
| Master DB | `POSTGRES_PASSWORD` và mật khẩu tương ứng trong `DATABASE_URL` |
| PostgreSQL nguồn | Mật khẩu owner, `SOURCE_MIGRATION_DATABASE_URL` và `SOURCE_DATABASE_URL` của reader |
| Dữ liệu nguồn thật | `SOURCE_ADAPTER=postgresql` |
| Kho file nguồn | `SOURCE_FILE_STORAGE_ROOT`: thư mục tuyệt đối có thật, đọc được |
| Kho file đích | `STORAGE_ROOT`: có quyền ghi, không trùng hoặc lồng với kho nguồn |
| API quản trị local | `ADMIN_ENABLED=true` để dùng các thao tác quản trị trên giao diện |
| MongoDB trên host | Mặc định `MONGO_URI=mongodb://127.0.0.1:27018` |
| OCR | `native` cho PDF có text; `tesseract` cho ảnh và PDF scan |

File nguồn được đọc theo cấu trúc `<SOURCE_FILE_STORAGE_ROOT>/<storage_bucket>/<storage_object_key>`.
Bucket/key lấy từ PostgreSQL, không nhập đường dẫn nguồn từ trình duyệt.
Mặc định chỉ nhập phiên bản nguồn `APPROVED`, cấu hình bằng `SOURCE_FILE_ALLOWED_STATUSES`.

Ví dụ đường dẫn Windows, cần thay bằng thư mục thực tế:

```dotenv
SOURCE_FILE_STORAGE_ROOT=D:/SourceLearningFiles
STORAGE_ROOT=./storage
```

### Cài Tesseract OCR trên Windows — làm một lần trên mỗi máy

DOCX/XLSX/TXT/CSV/PPTX và PDF có text không cần Tesseract. PNG/JPG/JPEG và các trang
PDF scan cần Tesseract thật cùng dữ liệu ngôn ngữ. Docker Compose hiện chỉ chạy CSDL,
không tự cài Tesseract vào Windows hoặc chạy OCR worker.

**Bước 1 — Cài phần mềm**

Tải bộ cài Windows 64-bit từ [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki),
nguồn được [tài liệu Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html#windows)
giới thiệu. Chạy bộ cài, chọn thư mục riêng và ghi lại đường dẫn cài đặt.
Không cài vào thư mục gốc dự án hoặc thư mục đang chứa dữ liệu khác.

Đường dẫn thường gặp:

- Cài cho máy: `C:\Program Files\Tesseract-OCR`.
- Cài riêng tài khoản: `C:\Users\<ten-user>\AppData\Local\Programs\Tesseract-OCR`.

Chọn ngôn ngữ **English (`eng`)** và **Vietnamese (`vie`)** nếu bộ cài có tùy chọn đó.
Không bắt buộc thêm PATH hệ thống vì ứng dụng dùng `TESSERACT_PATH` trong `.env`.

**Bước 2 — Kiểm tra và bổ sung dữ liệu ngôn ngữ**

Mở **PowerShell**. Đổi `$ocrInstallDir` thành thư mục bạn vừa cài:

```powershell
$ocrInstallDir = 'C:\Program Files\Tesseract-OCR'
$ocrExecutable = Join-Path $ocrInstallDir 'tesseract.exe'
if (-not (Test-Path -LiteralPath $ocrExecutable)) {
    throw 'Chưa tìm thấy Tesseract. Kiểm tra lại thư mục cài đặt.'
}
& $ocrExecutable --version
& $ocrExecutable --list-langs
```

Danh sách phải có cả `eng` và `vie`. Nếu thiếu, chạy tiếp đoạn dưới trong cùng PowerShell
để tải đúng file từ [kho tessdata_fast chính thức](https://github.com/tesseract-ocr/tessdata_fast).
Nếu báo thiếu quyền ghi trong Program Files, mở PowerShell bằng **Run as administrator**
và khai báo lại `$ocrInstallDir`, `$ocrExecutable` như trên.

```powershell
foreach ($ocrLanguage in @('eng', 'vie')) {
    $ocrModel = Join-Path $ocrInstallDir "tessdata\$ocrLanguage.traineddata"
    if (-not (Test-Path -LiteralPath $ocrModel)) {
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$ocrLanguage.traineddata" -OutFile $ocrModel
    }
}
& $ocrExecutable --list-langs
```

Các file phải nằm trực tiếp trong `Tesseract-OCR\tessdata`, không phải file HTML,
không đổi thành đuôi `.txt` và không tải lồng thêm một thư mục `tessdata` khác.

**Bước 3 — Cấu hình `.env` tại thư mục gốc dự án**

```dotenv
OCR_ENGINE=tesseract
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSERACT_LANGUAGES=vie+eng
```

Thay `TESSERACT_PATH` bằng đường dẫn thực trên máy của bạn; dùng dấu `/` và đặt trong
ngoặc kép nếu có khoảng trắng. Nếu cài riêng tài khoản, đường dẫn có dạng
`C:/Users/<ten-user>/AppData/Local/Programs/Tesseract-OCR/tesseract.exe`.
Mỗi biến chỉ khai báo một lần; thay dòng `OCR_ENGINE=native` cũ, không thêm bản trùng.
Không chép `.env` của người khác vì tài khoản Windows, đường dẫn và mật khẩu có thể khác.

**Bước 4 — Nạp lại cấu hình và thử ảnh**

Dừng backend và các worker đang chạy bằng `Ctrl + C`, rồi chạy lại các lệnh ở mục 4.
Backend kiểm tra runtime khi tạo job ảnh; OCR worker mới là nơi thực sự chạy Tesseract.
Worker không tự nạp `.env` sau khi bạn sửa file.

Trên giao diện: **Kho học liệu → Thêm học liệu** → chọn ảnh có chữ rõ → tạo học liệu
→ chờ xử lý → mở **Xem nội dung trích xuất**. Nếu học liệu đã được tạo:

- Chưa có job: bấm **Bắt đầu xử lý** sau khi cấu hình runtime xong.
- Job đã lỗi: bấm **Xử lý lại** sau khi khởi động lại OCR worker, không upload ảnh lần nữa.

Adapter dùng ảnh tạm tên ASCII để tránh lỗi đường dẫn tiếng Việt; thư mục tạm tự được
dọn sau xử lý. Không cần đổi tên thư mục dự án hoặc đổi `STORAGE_ROOT`.

Linux/Ubuntu có thể cài engine và hai ngôn ngữ bằng:

```bash
sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie
tesseract --list-langs
```

Sau đó dùng `TESSERACT_PATH=tesseract`, `OCR_ENGINE=tesseract` và `TESSERACT_LANGUAGES=vie+eng`.

`OCR_ENGINE=fake` chỉ dùng demo/test. API quản trị hiện dành cho local development;
`ADMIN_ENABLED` không thay thế xác thực admin khi triển khai public.

## 3. Khởi động database và migration

Bật Docker Desktop, rồi chạy tại thư mục gốc:

```bash
docker compose up --build -d
docker compose ps -a
```

Compose khởi động Master PostgreSQL, Source PostgreSQL và MongoDB.
Hai service `master-migrate`, `source-migrate` tự tạo/cập nhật schema, cần kết thúc với `Exited (0)`.
Compose không tự thêm học liệu hoặc tạo file nguồn.

Xem log nếu migration thất bại:

```bash
docker compose logs master-migrate source-migrate
```

Sau khi cập nhật code, cũng có thể chạy migration bằng Python trên host:

```bash
python -m alembic upgrade head
python -m alembic current
python -m alembic -c alembic_source.ini upgrade head
```

Master và Source dùng hai database riêng, không dùng URL Master để migrate Source.

### Reader PostgreSQL nguồn — thiết lập lần đầu

Reader phải có SELECT trên các bảng metadata, bao gồm `resource_versions`, và không có quyền ghi.
Các lệnh sau dùng tên mặc định trong `.env.example`; nếu đổi tên DB/role, sửa lệnh và script tương ứng.

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong cửa sổ psql, đặt mật khẩu riêng cho reader rồi thoát:

```text
\password etl_source_reader
\q
```

Cập nhật mật khẩu này vào `SOURCE_DATABASE_URL`. Không dùng owner/superuser làm reader.

### Kiểm tra MongoDB

```bash
python -m data_sync_etl.adapters.mongo.schema --check
```

Với database MongoDB mới, khởi tạo collection, validator và index:

```bash
python -m data_sync_etl.adapters.mongo.schema --apply
```

Nếu đã có dữ liệu, đọc kết quả `--check` và xử lý dữ liệu không hợp lệ trước khi apply.

## 4. Start backend, frontend và worker

**Mỗi process chạy trong một terminal riêng.** Với các terminal Python, vào thư mục gốc
và chạy `source .venv/Scripts/activate` trước các lệnh `python`/`etl`.

### Terminal 1 — Backend

```bash
python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

- Health: http://127.0.0.1:8000/health
- Swagger: http://127.0.0.1:8000/docs

### Terminal 2 — Frontend

Từ thư mục gốc:

```bash
cd frontend
npm.cmd run dev -- --host 127.0.0.1 --strictPort
```

Mở http://127.0.0.1:3000.
Vite đã proxy `/api`, `/admin`, `/health` đến backend cổng 8000.

### Terminal 3 — Master Sync worker

```bash
etl run-master-sync-worker --worker-id master-sync-1
```

Đồng bộ metadata theo lịch khi Auto được bật. Mặc định Incremental;
chọn Full cho nhóm thì worker chạy Full theo lịch.
Auto cần PostgreSQL nguồn/Master, reader hợp lệ, worker hoạt động và Full baseline thành công.

### Terminal 4 — File import worker

```bash
etl run-source-file-worker --worker-id source-file-1
```

Sau Master resources sync thành công, worker khám phá phiên bản, kiểm tra định dạng/hash,
nhập file và xếp hàng xử lý. Khi Auto resources bật, metadata phiên bản được đối soát định kỳ.
Khi tắt Auto, các job đã xếp hàng vẫn được xử lý.

### Terminal 5 — OCR/ETL worker

```bash
etl run-ocr-worker --worker-id ocr-1
```

Xử lý PDF, tài liệu native và ảnh OCR, chuẩn hóa nội dung và hoàn tất projection MongoDB.
Chỉ chạy backend HTTP hoặc mở trình duyệt không thay thế các worker.

## 5. Bắt đầu sử dụng

1. Kiểm tra cấu hình nguồn:

   ```bash
   etl source-file-precheck
   ```

   Kiểm tra DB, quyền đọc, kho file, hash và MongoDB. Precheck không chứng minh ETL hoàn tất.
   `OCR_WORKER=BLOCKED` có thể do chưa có registry heartbeat OCR; kiểm tra terminal và job thực tế.

2. Chạy Full baseline lần đầu bằng mục **Khởi tạo / đối soát** trên giao diện hoặc CLI:

   ```bash
   etl sync-master --stream all --full
   ```

3. Mở **Kho học liệu → Đồng bộ dữ liệu nguồn**, chọn chế độ và bật từng nhóm/tất cả.
4. Mở **Kho học liệu → Lỗi đồng bộ → Đồng bộ lại** để retry từng tệp hoặc theo lô.
   Tab **Lỗi đồng bộ** chứa các tệp không thể retry. Mỗi trang phân trang 7 tệp.
   Retry chỉ xếp hàng, worker thực hiện sau đó.

Đồng bộ nguồn không yêu cầu upload lại file. Upload thủ công dùng **Thêm học liệu** hoặc
cập nhật file trong chi tiết học liệu, rồi chuyển sang trang xử lý; trang này không nằm riêng
trong submenu. Xung đột phiên bản và nguồn đổi hash không được tự ghi đè.

| Định dạng | Nội dung được xử lý |
| --- | --- |
| PDF | Text trực tiếp; trang scan cần Tesseract |
| DOCX | Đoạn, tiêu đề và bảng văn bản |
| XLSX | Sheet/hàng/ô, đọc giá trị cache; không tính công thức |
| TXT/CSV | UTF-8 hoặc UTF-8 BOM, giữ vị trí dòng/hàng |
| PPTX | Văn bản slide và bảng, không OCR ảnh nhúng |
| PNG/JPG/JPEG | OCR chữ bằng Tesseract, không phân tích cảnh/biểu đồ |

MP3/MP4 không được tiếp nhận hoặc xử lý; học liệu đã lưu trước đó vẫn được giữ nguyên.

Luồng nguồn hỗ trợ các định dạng có xử lý nội dung ở bảng trên.
Xem [giới hạn định dạng và trích xuất](docs/multiformat_etl.md).

## 6. Lệnh thường dùng

### Metadata Projection Master → MongoDB

Sau sync thành công, hệ thống đối soát metadata `users`/`resources` từ Master.
Tên mode `EXTENSION_SYNC` được giữ để tương thích, nhưng hiện chỉ kiểm tra thiếu
nguồn: Master chưa có `bio`, `description`, `authors`, `tags`, chưa có writer extension.
Hệ thống dùng COUNT, báo `PARTIAL_SOURCE_FIELDS` / `skipped_no_extension`, không ghi Mongo.
Mongo lỗi không rollback Master; chạy lại bằng:

```bash
etl project-master-mongo --stream all --full
```

Identity-only chỉ bật bằng `MASTER_MONGO_IDENTITY_BOOTSTRAP=true` và danh sách
`MASTER_MONGO_IDENTITY_IDS` đã xác nhận `_id=Master.id`; mặc định tắt, không tự bật.
ID chưa duyệt được tính `skipped_unapproved`, không phải conflict. Không có ID duyệt
phù hợp thì báo `NO_APPROVED_IDS`; `source.mongo_id` khác ID mới là mapping conflict.
Document đã có giữ
nguyên các field mở rộng và timestamp. Không thay UI/API, OCR hoặc lịch Auto.
Xem [chính sách mapping, audit và vận hành](docs/master_metadata_projection.md).

Trong `mongosh`, chọn database đúng theo `MONGO_DATABASE`, rồi kiểm tra:

```javascript
db.users.countDocuments({})
db.learning_resources.countDocuments({})
```

Số document identity-only không chứng minh đã có profile/description đầy đủ.

```bash
etl --help
etl sync-master --stream resources --incremental
etl source-file-precheck
etl run-master-sync-worker --worker-id master-sync-test --once
etl run-source-file-worker --worker-id source-file-test --once
etl run-ocr-worker --worker-id ocr-test --once
etl etl-status --resource-version-id VERSION_ID
etl resume-etl --resource-version-id VERSION_ID
etl recover-stale-ocr-tasks
```

Thay `VERSION_ID` bằng ID phiên bản đích. `--once` chạy một lượt rồi thoát.
Worker im lặng khi không có việc là bình thường.

Muốn thử fixture JSON trên database demo riêng, đặt `SOURCE_ADAPTER=json`, sau đó chạy:

```bash
etl sync-master --stream all --full
etl demo-run --fixture-resource-id resource-1
```

JSON không hỗ trợ Auto Sync hoặc nhập file từ PostgreSQL nguồn.

## 7. Kiểm thử và build

### Sau khi pull code mới

Tại thư mục gốc, activate `.venv` và cài lại dependency:

```bash
python -m pip install -e '.[dev]'
```

Không chép đè `.env` đang dùng. Đối chiếu `.env.example`, bổ sung các biến còn thiếu;
nếu dùng ảnh/PDF scan, làm đủ các bước cài Tesseract ở mục 2. Chạy database/migration
ở mục 3, `npm.cmd install` trong frontend khi dependency thay đổi, rồi restart backend
và các worker. Pull code hoặc backend `--reload` không tự restart các worker.

Backend, tại thư mục gốc:

```bash
python -m pytest -q
python -m ruff check src tests alembic alembic_source scripts
```

Test tích hợp đọc `TEST_DATABASE_URL`, `TEST_SOURCE_DATABASE_URL`, `TEST_MONGO_URI` từ
môi trường process theo từng bộ test. Dùng database test riêng; thiếu cấu hình sẽ bị SKIP,
không được tính là kiểm thử tích hợp thành công.

Frontend:

```bash
cd frontend
npm.cmd run build
npm.cmd run lint
```

## 8. Lỗi thường gặp

| Hiện tượng | Cách kiểm tra |
| --- | --- |
| 422 khi đổi Full | Restart backend để nạp contract mới; restart Master worker khi code worker thay đổi |
| 500 sau cập nhật code | Chạy `python -m alembic upgrade head` để bổ sung bảng/cột mới |
| 503 khi đọc phiên bản nguồn | Kiểm tra URL nguồn và SELECT trên `resource_versions` |
| Công tắc Auto bị khóa | Kiểm tra PostgreSQL nguồn, reader và Master worker |
| Thiếu baseline | Chạy Full Sync thành công lần đầu |
| Kho nguồn chưa sẵn sàng | Kiểm tra root tuyệt đối, quyền đọc và kho nguồn/đích tách biệt |
| PDF scan lỗi OCR | Kiểm tra Tesseract, ngôn ngữ và `OCR_ENGINE` |
| `BLOCKED_OCR_RUNTIME` hoặc OCR ảnh cần Tesseract | Kiểm tra `TESSERACT_PATH`, `--list-langs` có `vie`/`eng`, `OCR_ENGINE=tesseract`; restart backend/worker |
| `tesseract` không được nhận diện trong terminal | Chạy bằng đường dẫn đầy đủ như mục 2; không bắt buộc sửa PATH |
| OCR lỗi với đường dẫn tiếng Việt | Pull bản adapter ảnh tạm mới, restart OCR worker rồi Xử lý lại; không đổi kho file |
| File đã nhập, chưa hoàn tất | Kiểm tra OCR worker, MongoDB và finalization |
| Cổng Docker đã được dùng | Kiểm tra `docker compose ps -a`, tránh chạy stack trùng cổng |

Nếu thiếu quyền đọc phiên bản, đăng nhập **database nguồn** bằng owner/admin và chạy
(thay tên reader nếu khác):

```sql
GRANT SELECT ON TABLE public.resource_versions TO etl_source_reader;
COMMIT;
```

Đổi mật khẩu trong `.env` không tự đổi mật khẩu PostgreSQL đã tồn tại trong volume.
Sau khi sửa cấu hình, restart backend và các worker liên quan.

## 9. Dừng và chạy lại

Nhấn `Ctrl + C` tại các terminal backend, frontend và worker. Dừng database:

```bash
docker compose stop
```

Lần sau chạy lại `docker compose up --build -d` và các lệnh start ở mục 4.
Không dùng `docker compose down -v` để sửa lỗi migration: lệnh đó xóa volume dữ liệu.

etl run-source-file-worker --worker-id files-test-1
etl run-ocr-worker --worker-id ocr-test-1
etl run-master-sync-worker --worker-id master-sync-1

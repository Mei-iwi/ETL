# ETL — Đồng bộ và xử lý học liệu

Đồng bộ PostgreSQL nguồn → Master Data; đối soát metadata tại chỗ trong **một MongoDB
database `etl_content`**, không copy Mongo nguồn → đích;
nhập file local, trích xuất/OCR, chuẩn hóa và lưu Content Units. Frontend React hỗ trợ
quản lý, đồng bộ và retry. Nhóm lập chỉ mục bổ sung Chunk theo cửa sổ từ, BoW/TF và
embedding ngữ nghĩa bằng model local tùy chọn. Chưa triển khai Search hoặc đánh giá;
không sử dụng `content_bags` để lưu Chunk.

Master Sync chỉ đồng bộ metadata; nội dung hoàn tất khi đạt `CONTENT_READY`.
`COMPLETED` trên API/giao diện đồng bộ là PostgreSQL Master, không xác nhận Mongo
metadata thành công. `CONTENT_READY` không đồng nghĩa đã lập chỉ mục tìm kiếm.
Hỗ trợ **PDF, DOCX, XLSX, TXT, CSV, PPTX, PNG, JPG, JPEG**; không tiếp nhận/xử lý MP3/MP4.

### Lập chỉ mục học liệu

Trang `/library/indexing` chọn học liệu và phiên bản. `POST
/api/v1/resource-versions/{id}/index` chỉ xếp hàng `INDEX_BUILD` vào `etl_stage_runs`
sau `CONTENT_READY`; worker OCR hiện có xử lý lúc nhàn rỗi. Không có daemon mới,
không đồng bộ nguồn hay chạy OCR ngầm khi bấm lập chỉ mục.

Model là tùy chọn: `python -m pip install -e '.[embeddings]'` khi người vận hành
đồng ý cài runtime. Provision model sentence-transformers đa ngôn ngữ vào thư mục
local trước; tạo `etl-model-revision.txt` bên trong, chứa revision đã provision.
Cấu hình `INDEX_EMBEDDING_MODEL_PATH`, `INDEX_EMBEDDING_MODEL_ID`,
`INDEX_EMBEDDING_MODEL_VERSION` (khớp file revision), `INDEX_EMBEDDING_DIMENSION`
(đúng model). Không dùng `VECTOR_DIMENSION=256` của content_bags để ép chiều model.
Runtime chỉ đọc model local, không tự download lúc startup/worker. Model thiếu,
không đọc được hoặc sai chiều sẽ bị chặn; không có vector giả thay thế.

Mongo installer bổ sung duy nhất collection `indexed_chunks`, hai index theo
version/run/sequence và version/run/unit; giữ nguyên 5 collection cũ.
Chạy kiểm tra trước: `python -m data_sync_etl.adapters.mongo.schema --check`.
Chỉ chạy cùng lệnh với `--apply` thay `--check` trên DB test/demo cô lập, hoặc sau
khi operator duyệt backup/schema của DB thật. App không tự cài schema lúc startup.
Không cần PostgreSQL migration cho nhóm lập chỉ mục.

`CONTENT_READY` chỉ xác nhận nội dung ETL; chỉ mục chỉ READY sau khi worker ghi và
verify đủ Chunk/BoW/vector cùng fingerprint, rồi đánh dấu stage COMPLETED.
Review/eligibility không được nâng quyền: có thể xem Chunk chưa duyệt trong workspace
quản lý, không có nghĩa được công bố trong Search. Lỗi index không đổi kết quả OCR.

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

Master và Source là hai DB riêng. Head Master hiện tại `0015`: `0013` thêm reference
users/resources; `0014` thêm reference grade/version/unit; `0015` thêm provenance và
audit mất nguồn. Dữ liệu cũ UNKNOWN, không auto-backfill ownership.
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

### Một MongoDB database — metadata tại chỗ và ETL

Cấu hình mục tiêu (operator đối chiếu `.env` hiện tại, backup và phê duyệt trước khi đổi):

```dotenv
SOURCE_ADAPTER=postgresql
MONGO_URI=mongodb://127.0.0.1:27018
MONGO_DATABASE=etl_content
MONGO_METADATA_MODE=shared
SOURCE_MONGO_ENABLED=false
MASTER_MONGO_IDENTITY_BOOTSTRAP=false
```

`shared` là mặc định. Hai cờ true gây config conflict trước khi tạo client/storage;
không tự sửa `.env`. SOURCE_MONGO_URI/SOURCE_MONGO_DATABASE cũ được bỏ qua ở mode
shared, không kết nối `source_metadata`. Restart backend/workers sau cấu hình đã duyệt.
Mode `separate` chỉ giữ cho tương thích/test cũ, không phải kiến trúc mục tiêu.

Trong etl_content, `users`/`learning_resources` là **dữ liệu mở rộng gốc** do đầu vào
được quản lý nhập trực tiếp, không phải clone Master. `_id` String bằng PG nguồn.mongo_id
và Master.mongo_id, có thể khác PG PK. Metadata có thể nhập trước/sau PG sync.
Thêm document Mongo không chạy PG sync ngầm hoặc kích hoạt Incremental/CDC.
`ocr_pages`/`mongo_content_units` là đầu ra ETL; `content_bags` giữ nguyên, runtime không ghi.

Master Full/Incremental/Auto chỉ sync PG, log SKIP_PG_SHARED_METADATA_VALIDATION để
tránh quét metadata ở mỗi tick. Hai CLI sau **chỉ đọc**, không bootstrap hoặc copy-self:

```bash
etl sync-mongo-metadata --stream all --full
etl project-master-mongo --stream all --full
```

Report `mode=VALIDATE_SHARED`, SHARED_MONGO_VALIDATED/PARTIAL/FAILED và counters
matched/missing_mapping/missing_document/missing_extension/invalid_document/conflicts.
Inserted/updated luôn 0. Quét Master theo keyset (100–1000 mỗi batch), verifier PG
nguồn xác nhận mapping, subject active/nondeleted ở Source + Master. Bỏ qua
inactive/deleted/ARCHIVED; DRAFT chỉ được kiểm tra, không tự public. Không full-scan
Mongo để báo orphan count; không giữ PG transaction trong RPC Mongo.

Một extension hữu ích là string không trắng hoặc mảng không rỗng sau validate/
normalization. Chỉ ID/ngày, explicit NULL, chuỗi trắng, authors/tags rỗng không đủ.
Không tự thêm bio/description/tags, không remap ID/ObjectId hoặc tạo doc khi thiếu
mapping. Timestamps và BSON metadata giữ nguyên, kể cả field ngoài ownership.
Metadata thiếu/outage không rollback PG. Retry CLI kiểm tra Full lại, không có CDC.
Document identity-only/DB source_metadata cũ không bị xóa/di chuyển/enrich tự động.

#### Precheck read-only và vận hành có phê duyệt

Read-only: `git status --short`, `docker compose ps -a`, `python -m alembic current`,
`python -m alembic heads`, `python -m alembic -c alembic_source.ini current`.
Kiểm backup/restore, file roots, quyền và 1–3 mẫu PG id→Master.mongo_id→Mongo String _id
(không dump PII); PG Source/Master query `count(*)`, `count(mongo_id)` theo bảng tương ứng.

```bash
docker compose exec -T mongo mongosh --quiet etl_content --eval "printjson({users:db.users.countDocuments({}),resources:db.learning_resources.countDocuments({}),ocr_pages:db.ocr_pages.countDocuments({}),units:db.mongo_content_units.countDocuments({}),bags:db.content_bags.countDocuments({})})"
```

Không có `02_import_mongo_source.js` trong repo. Nếu operator có file ngoài repo,
phải sửa **nội dung** script về duy nhất `db.getSiblingDB('etl_content')`, bỏ SOURCE_DB/
TARGET_DB và guard cũ. Chỉ insert document vắng; cùng ID nhưng payload khác phải báo
conflict, không overwrite. Đổi DB trên lệnh mongosh không sửa getSiblingDB trong JS.
Không viết lại ZIP/seed không được cung cấp, không tự chạy import/live sync.

Sau phê duyệt backup/config/queue: PG seed và metadata import trực tiếp vào etl_content
(nếu cần, do operator quản lý) → `etl sync-master --stream all --full` hoặc
`--incremental` → shared verify → file worker/OCR worker `--once` → `etl etl-status`.
`--once` không lọc version, phải duyệt phạm vi queue trước. Đối soát cùng target
version/job/page/generation/current; checksum/char_count/IDs đúng, replay không trùng,
metadata và content_bags snapshot không đổi. Scan Tesseract chưa test phải NOT_RUN.
Không dùng compose up như read-only vì có migration side effect; không down -v.

### CLI thường dùng

#### Đối soát bản ghi mất nguồn — không xóa dữ liệu

Full/Incremental vẫn upsert bản ghi hiện diện, không tự phát hiện/apply hard delete.
Soft delete đi theo trạng thái nguồn và timestamp; users nguồn chỉ có is_active,
grades dùng grade_is_active/grade_deleted_at ở Master. Trigger liên kết nguồn hiện
touch learning_resources.updated_at khi thêm/sửa/xóa quan hệ; giữ replay/GREATEST.

Migration mới `0015` thêm source_origin/source_state_hash/source_missing_at cho
users/resources/subjects/grades Master; không sửa 15 bảng nguồn. Bản ghi PG insert mới
POSTGRESQL, API-created resource LOCAL, dữ liệu cũ UNKNOWN. Không auto-backfill chỉ
vì cùng ID/mongo_id. Unknown/local trùng Source PK bị BLOCKED_SOURCE_OWNERSHIP;
operator phải audit và duyệt ownership riêng trước khi sync, không bulk đổi marker.
Fingerprint trạng thái bảo vệ thay đổi nội bộ; nguồn tái xuất hiện phải giữ created_at
và mapping đã duyệt. Cùng PK nhưng thực thể mới/remap sẽ bị chặn, không tự phục hồi.

Pre-flight READ-ONLY: kiểm `python -m alembic current`/`heads`, backup/restore, quyền
reader và đúng DB. Code head 0015 cần operator duyệt migration; **không tự chạy**
`python -m alembic upgrade head` trên DB đang dùng. Sau migration đã duyệt/restart:

```bash
etl reconcile-master --stream resources --dry-run
etl reconcile-master --stream all --dry-run
```

Preview không ghi Master/Mongo/checkpoint. Report có source_count, pages, fingerprint,
known-source/local/unknown exclusions, candidates/already-inactive và tối đa 20 ID mẫu.
Quét bảng gốc trong read-only REPEATABLE READ, kiểm RLS/view/dependency/completeness;
không suy parent bị xóa từ aggregate JOIN. Nguồn rỗng, giảm mạnh, lỗi trang/quyền/schema,
local mutation hoặc vượt ngưỡng → BLOCKED. JSON adapter không được reconcile/apply.

Chỉ sau khi operator duyệt plan fingerprint và phạm vi ảnh hưởng:

```bash
etl reconcile-master --stream resources --apply --fingerprint <APPROVED_FINGERPRINT>
```

Apply quét lại, so fingerprint, khóa Master, đọc nguồn lại, kiểm toàn write-set trước
mọi UPDATE; commit một transaction giới hạn (mặc định tối đa 100 row, tỷ lệ 10%).
Vượt ngưỡng không apply một phần. Không tự tăng RECONCILE_MAX_AFFECTED/MAX_RATIO để
vượt guard. PG source/target khác DB nên không có atomic distributed snapshot: nguồn
có thể tái xuất hiện sau lần kiểm cuối, khi đó Full/Incremental sau sẽ restore nếu
ownership/identity/state còn hợp lệ. Không tuyên bố CDC hoặc loại bỏ hoàn toàn race.

Đích chỉ inactive và deleted_at/grade_deleted_at nếu chưa có. source_missing_at là
thời điểm **ghi nhận mất nguồn**, không phải thời điểm người dùng xóa. Giữ deleted_by,
created_at/PK/mongo_id, quan hệ, resource_versions/jobs/tasks/content_units, toàn bộ
Mongo users/resources/ocr_pages/units/content_bags. Row đã inactive không viết lại
ngày xóa; chạy lại preview/apply mới idempotent. Reconcile không đổi counters/mode PG.

Sau apply: SELECT trạng thái/provenance, đối chiếu count/hash lịch sử PG và BSON
Mongo; API detail/version/content và enqueue/retry không trả/khởi tạo nội dung đã
thu hồi. Job đang chạy/finalization được giữ theo state machine, không kill/xóa history.
Không tự cleanup hoặc rollback downgrade (guard chặn khi có audit/mapping). Restore
chỉ từ Source hợp lệ hoặc quy trình operator đã duyệt; không reset marker tùy tiện.

Giao diện phân biệt cấu hình Auto với từng lần chạy thực tế (mode/thời điểm/run ID).
Nút **Đồng bộ Full** gọi full=true; **Chạy Incremental** gọi full=false và có xác nhận.
Đổi dropdown chỉ lưu lịch Auto, không chạy ngay. `Đã đọc (ứng viên)` gồm cả bản ghi
replay/SKIPPED, không phải số thêm/cập nhật. Với PostgreSQL, replay mặc định 300 giây
**tính từ checkpoint timestamp**, không phải cửa sổ 300 giây gần giờ hiện tại;
chờ lâu không bảo đảm đọc 0. Giữ GREATEST timestamps của parent và đối soát Full định kỳ
cho writer commit muộn quá replay. Không thay số liệu hoặc tắt replay để làm đẹp bảng.

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

Test logic chế độ/kết quả/poll không cần framework UI mới (đã chạy với Node 22.17):

```bash
node --experimental-strip-types --test tests/masterSyncResults.test.mjs
```

Quy trình nghiệm thu một MongoDB database nằm ở mục 5 phía trên.
QA không fallback sang database ứng dụng; tên database test phải chứa thành phần
`test` riêng (ví dụ `etl_test`, `etl_source_test`). Không dùng `docker compose up`
để pre-flight read-only vì service migration có thể ghi database.

## 8. Lỗi thường gặp và dừng hệ thống

| Hiện tượng | Cách xử lý |
| --- | --- |
| API 500 sau pull, báo thiếu bảng/cột | Kiểm tra traceback, đúng URL Master và migration head; không phải mọi lỗi 500 đều do schema |
| 503 khi đọc nguồn | Kiểm tra DB/reader, SELECT trên resource_versions, kho file và thông báo lỗi cụ thể |
| Auto không chạy | Kiểm tra baseline, công tắc, Master worker và chu kỳ; restart sau đổi config |
| PARTIAL, metadata thiếu | Kiểm tra mapping và đầu vào metadata thật trong etl_content; không bật bootstrap để lấp thiếu |
| SHARED_MONGO_CONFLICT_* | shared yêu cầu SOURCE_MONGO_ENABLED=false và MASTER_MONGO_IDENTITY_BOOTSTRAP=false; operator phê duyệt cấu hình rồi restart |
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

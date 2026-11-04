# ETL — Kho học liệu số

Đồng bộ metadata học liệu, xử lý OCR và lập chỉ mục tìm kiếm BoW, Vector, Hybrid.

## Kiến trúc

| Thành phần | Nơi chạy | Vai trò |
| --- | --- | --- |
| PostgreSQL SOURCE | Neon | Metadata nguồn, phiên bản, OCR jobs/page tasks và content units |
| PostgreSQL MASTER | **Một container Docker local** | Metadata, mapping, read-model và trạng thái; migration `0020` |
| MongoDB | Atlas, database `etl_content` | Đúng 5 collection: `users`, `learning_resources`, `ocr_pages`, `mongo_content_units`, `content_bags` |
| Tệp gốc | Object Storage / Google Drive | Truy xuất theo quyền của nguồn |
| API, giao diện, worker | Windows host | Đồng bộ, xử lý và phục vụ người dùng |

Docker trong hướng dẫn này **chỉ chạy PostgreSQL MASTER**. SOURCE dùng Neon hiện hữu, MongoDB dùng Atlas; không tạo hai database đó ở local.

## 1. Chuẩn bị

Cần Python 3.12, Node.js 22.12+ (khuyến nghị 24), Docker Desktop và Git. Cài Tesseract kèm `vie`/`eng`; thêm LibreOffice cho tệp Office, FFmpeg/ffprobe cho media.

Chạy PowerShell từ gốc repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install 'torch==2.14.1+cpu' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[dev,embeddings,google-drive,media,translation]'
python -m pip check
npm.cmd --prefix frontend ci
```

Không dùng media có thể cài `.[dev,embeddings,google-drive]` như bản base. Các lệnh `python` dưới đây chạy trong venv đã kích hoạt. Dùng `npm.cmd` để tránh execution policy của PowerShell.

## 2. Cấu hình

Chỉ sao chép mẫu nếu chưa có `.env`:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

| Vị trí | Nội dung |
| --- | --- |
| `.env` | Kết nối database, đường dẫn, tùy chọn API/worker |
| `ETL_Secrets/runtime/` | Khóa xác thực, CA và owner contract đã phê duyệt |
| `Models/` | Model và manifest |
| `storage/` | Dữ liệu làm việc theo cấu hình |

Các vị trí riêng tư này được Git bỏ qua. Không cần `D:\etl\private\connections.json`; bỏ `CONNECTIONS_FILE`/`ETL_CONNECTIONS_FILE` nếu chuyển sang cấu hình trực tiếp. Kiểm tra cả biến môi trường của tiến trình để tránh giá trị cũ ghi đè `.env`.

Thiết lập tối thiểu, thay các placeholder bằng cấu hình riêng tư:

```dotenv
MASTER_ONLY_PROJECT=etl-master-only
MASTER_ONLY_PORT=5434
POSTGRES_DB=etl
POSTGRES_USER=etl
POSTGRES_PASSWORD=CHANGE_ME
DATABASE_URL=postgresql+psycopg://etl:CHANGE_ME@127.0.0.1:5434/etl

SOURCE_ADAPTER=postgresql
SOURCE_NAMESPACE=postgresql:learning
SOURCE_DATABASE_URL=REPLACE_WITH_PRIVATE_NEON_READER_URI
MONGO_URI=REPLACE_WITH_PRIVATE_ATLAS_URI
MONGO_DATABASE=etl_content
MONGO_METADATA_MODE=shared
SOURCE_MONGO_ENABLED=false

INDEX_EMBEDDING_MODEL_PATH=Models/bge-m3
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
```

`DATABASE_URL` phải khớp user/database/password MASTER; percent-encode mật khẩu trong URI. Neon reader dùng quyền đọc và TLS/CA theo cấu hình được cấp; SOURCE writer dùng credential riêng. Không đưa URI thật vào file mẫu hoặc log.

Khai báo đường dẫn contract, khóa và công cụ trong `.env`: `SOURCE_OCR_CONTRACT_FILE`, `SESSION_SIGNING_KEY_FILE`, `AUTH_PROXY_KEY_FILE`, `TESSERACT_PATH`, `OFFICE_PDF_EXECUTABLE`, `FFMPEG_PATH`, `FFPROBE_PATH`. Xem đầy đủ tại [.env.example](.env.example).

## 3. Docker: chỉ khởi tạo PostgreSQL MASTER

Compose dùng image có sẵn `postgres:16-bookworm`, không cần build Dockerfile. Chỉ định rõ [compose.master-only.yml](deploy/compose.master-only.yml):

```powershell
# Kết quả phải chỉ có master-postgres.
docker compose --env-file .env -f deploy/compose.master-only.yml config --services

# Tải image và khởi động duy nhất database MASTER.
docker compose --env-file .env -f deploy/compose.master-only.yml pull master-postgres
docker compose --env-file .env -f deploy/compose.master-only.yml up -d master-postgres

# Kiểm tra container và PostgreSQL.
docker compose --env-file .env -f deploy/compose.master-only.yml ps
docker compose --env-file .env -f deploy/compose.master-only.yml exec master-postgres pg_isready -U etl -d etl
```

Ví dụ trên dùng user/database `etl`; thay tương ứng nếu cấu hình khác. Kết nối từ host tại `127.0.0.1:5434`; `5432` là cổng trong container. Dữ liệu nằm trong named volume `master-only-data` của Compose project.

Với **MASTER mới/trống đã xác minh**, tạo schema:

```powershell
python -m alembic upgrade 0020
python -m alembic current
```

Revision phải là `0020`. Không dùng `upgrade head` cho luồng hiện tại; không chạy migration/seed lên SOURCE.

Dừng và bật lại mà giữ dữ liệu:

```powershell
docker compose --env-file .env -f deploy/compose.master-only.yml stop master-postgres
docker compose --env-file .env -f deploy/compose.master-only.yml up -d master-postgres
```

Cập nhật mã nguồn không cần xóa volume hoặc database. Đổi `POSTGRES_PASSWORD` trong `.env` không đổi mật khẩu của database đã khởi tạo. Xem thêm [hướng dẫn database](deploy/DATABASE_SETUP.md).

## 4. Xác thực, model và Atlas

Giữ nguyên khóa và contract nếu hệ thống đã được cấu hình. Với cài mới dùng `AUTH_MODE=single_admin`, tạo khóa một lần bằng CLI; mật khẩu được nhập ẩn:

```powershell
python -m data_sync_etl.security.cli init --directory ETL_Secrets/runtime
```

Điền `ADMIN_LOGIN_EMAIL`, `ADMIN_PASSWORD_HASH_FILE`, `SESSION_SIGNING_KEY_FILE`, `AUTH_PROXY_KEY_FILE` theo kết quả. Để dùng admin local, đặt `ADMIN_ENABLED=true`, `AUTH_ORIGIN=http://127.0.0.1:3000`, `AUTH_DEPLOYMENT_PROFILE=localhost`. Không có mật khẩu mặc định.

`AUTH_MODE=master_users` dùng tài khoản đồng bộ từ SOURCE: cần FULL sync users và worker users-refresh. Service OCR phải mapping về đúng SOURCE actor có quyền; admin local không tự thay thế danh tính này.

Kiểm tra model BGE-M3 đã có:

```powershell
python deploy/runtime/model.py verify --directory Models/bge-m3 --manifest Models/bge-m3/etl-model-manifest.json --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
```

Nếu chưa có model, dùng action `prepare` thay `verify` và thêm `--accept-model-license` sau khi xem, chấp nhận giấy phép.

Atlas cần đúng 5 collection trong bảng kiến trúc, validator `strict/error`, indexes theo contract và embedding 1024 chiều. Kiểm tra schema hiện có:

```powershell
python -m data_sync_etl.adapters.mongo.schema --inventory
python -m data_sync_etl.adapters.mongo.meaning_schema --check
```

Đây là lệnh kiểm tra; database mới cần được chuẩn bị theo contract bằng tài khoản được cấp quyền.

## 5. Đồng bộ và chạy hệ thống

SOURCE phải có schema/dữ liệu và source registry ID đã đăng ký. Ví dụ với `postgresql`:

```powershell
python -m data_sync_etl.cli sync-master --full --stream all --source-id postgresql
```

### Chạy local: frontend, backend và 4 worker

Các tiến trình dưới đây chạy trực tiếp trên Windows, **không cần container ứng dụng**. Cần cài dependencies, chuẩn bị model/khóa và cấu hình `.env` ở các bước trên. Database MASTER phải đang truy cập được qua `DATABASE_URL` (ở mô hình hiện tại, chỉ MASTER chạy Docker); SOURCE Neon và MongoDB Atlas phải sẵn sàng.

Các lệnh khởi động trong bảng giữ nguyên bản base tại commit `9c4b743` (`HEAD` khi đối chiếu), gồm cả `--worker-id`. Mở **6 terminal PowerShell** tại gốc repository. Trong mỗi terminal chạy `.\.venv\Scripts\Activate.ps1`, sau đó chạy một lệnh tương ứng; giữ terminal mở khi sử dụng hệ thống.

| Tiến trình | Lệnh |
| --- | --- |
| API | `python -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| React/Vite | `npm.cmd --prefix frontend run dev` |
| Đồng bộ metadata | `python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1` |
| Xác minh metadata/tham chiếu nguồn (không import byte gốc) | `python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1` |
| Trích xuất/OCR/ASR và hoàn thiện chỉ mục cho LOCAL; SOURCE remote chỉ qua capability RAM | `python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1` |
| Đối soát theo lịch đã lưu, đồng bộ đúng ID lệch | `python -m data_sync_etl.workers.reconciliation` |

Nếu dùng `AUTH_MODE=master_users`, mở **thêm một terminal** cho worker làm mới danh tính người dùng:

| Tiến trình bổ sung | Lệnh khởi động local |
| --- | --- |
| Làm mới danh tính khi `AUTH_MODE=master_users` | `python -m data_sync_etl.cli run-users-refresh-worker --source-id postgresql` |

Worker users-refresh là hướng dẫn bổ sung cho cấu hình hiện tại, không thuộc bảng 4 worker của bản base. Các hướng dẫn `.env` trong dự án, Docker chỉ MASTER và FULL sync với `--source-id postgresql` được giữ theo yêu cầu cấu hình mới.

Bật lịch Auto Sync trên giao diện nếu cần đồng bộ định kỳ. Nhấn `Ctrl+C` trong từng terminal để dừng. Sau khi đổi `.env`, khởi động lại các tiến trình liên quan bằng các lệnh tương ứng ở trên.

- Giao diện: <http://127.0.0.1:3000>
- Master Data: <http://127.0.0.1:3000/systems/master-data>
- API health: <http://127.0.0.1:8000/health>

### Chạy ứng dụng bằng Docker

Với cấu hình local đã hoạt động và đủ model/contract được phê duyệt, chuẩn bị cấu hình container từ `.env`:

```powershell
python deploy/scripts/prepare-project-docker.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build -EnvFile docker.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -EnvFile docker.env -Workers
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status -EnvFile docker.env
```

Mở <http://127.0.0.1:8080>. Profile `neon-atlas` dùng MASTER đang chạy trên network `etl-master-only_default`, SOURCE Neon và MongoDB Atlas; không khởi tạo database hay migration. `docker.env` giữ cấu hình container, còn credential/contract được mount từ `ETL_Secrets/`; model mount chỉ đọc từ `Models/`. Script chuẩn bị giữ phạm vi quyền trong `.env` và contract hiện có, không tự cấp quyền. Khi đổi `.env`, chạy lại bước chuẩn bị và Start; khi đổi code, Build rồi Start. Tránh chạy đồng thời worker local khi đã chuyển sang Docker.

## 6. OCR và chỉ mục tự động

Luồng xử lý: metadata → kiểm tra OCR → reuse hoặc xử lý trang thiếu → normalize/chunk → content units → ContentBag 1024D → chỉ mục READY → Search/UI.

Cần bật `OCR_COMPONENT_PIPELINE_ENABLED=true`, cấu hình `SOURCE_OCR_CONTRACT_FILE` và `INDEX_AUTO_ENABLED=true` sau khi xác minh schema/quyền. Contract owner phải cho phép namespace, lifecycle và service actor; vẫn kiểm tra capability đọc/xử lý/lập chỉ mục, mapping, hash, quyền tệp và provenance.

[Contract mẫu](deploy/config/source-ocr-contract.example.json) không thay thế sự phê duyệt của owner. Lifecycle `PENDING_PRE_REVIEW` chỉ được xử lý khi đã được phê duyệt. DRAFT đã xử lý vẫn chịu ACL, không tự xuất hiện trong tìm kiếm công khai. Job bên ngoài thiếu provenance cần proof/contract hợp lệ.

```powershell
# Kiểm tra cấu trúc contract; không tự cấp quyền.
python -m data_sync_etl.cli validate-source-ocr-contract --file ETL_Secrets/runtime/source-ocr-contract.json --namespace postgresql:learning

# Đánh giá lại tối đa 10 phiên bản theo policy hiện hành, không gọi /process.
python -m data_sync_etl.cli audit-source-ocr --limit 10 --force
```

## 7. Chuyển máy và xử lý lỗi

Mang theo mã nguồn, `.env`, `ETL_Secrets/`, `Models/`; cài lại dependencies và công cụ hệ thống, chỉnh đường dẫn theo máy mới. Giữ bản sao MASTER và `storage/` nếu muốn tiếp tục trạng thái hiện tại. MASTER mới cần migration `0020`, đồng bộ lại và xác minh mapping/provenance trước khi reuse OCR cũ.

| Hiện tượng | Kiểm tra |
| --- | --- |
| Không kết nối MASTER | Container healthy, cổng 5434, user/database/password khớp `.env` |
| Lỗi Neon/Atlas | Credential, DNS/TLS/CA, network access và quyền; không fallback sang DB local |
| `AUTH_MASTER_STALE` | FULL users và worker users-refresh |
| Chờ quyền xử lý | Namespace/lifecycle/actor trong owner contract và quyền ứng dụng |
| Model chưa READY | Đường dẫn, revision, manifest; chờ preload trước Vector/Hybrid |
| Không đọc/xử lý được tệp | Mapping, hash, MIME, quyền storage và provenance |

Dùng CLI và worker trong mã nguồn để vận hành; các script chẩn đoán một lần trong `.local/` không phải thành phần khởi động.

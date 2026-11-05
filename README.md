# ETL — Kho học liệu số

Đồng bộ metadata học liệu, xử lý OCR và lập chỉ mục tìm kiếm BoW, Vector, Hybrid.

Hướng dẫn dành cho Windows PowerShell, chạy lệnh tại thư mục gốc repository. Có hai cách vận hành: chạy API/frontend/worker trực tiếp trên Windows hoặc chạy chúng bằng Docker. Cả hai dùng SOURCE Neon hiện hữu, một PostgreSQL MASTER Docker và MongoDB native Windows.

| Bạn cần làm gì? | Đọc phần |
| --- | --- |
| Cài hệ thống trên máy mới | Bước 1–4, rồi chọn cách chạy ở bước 5 |
| Bật lại hệ thống đã cấu hình | Bảng khởi động nhanh ngay dưới đây |
| Bật đồng bộ và OCR tự động | Bảng worker ở bước 5 và điều kiện tại bước 6 |
| Bật Neo4j và graph worker | Bước 7 |
| Kiểm tra lỗi, dừng hoặc chuyển máy | Bước 8–9 |

## Khởi động nhanh trên máy đã cấu hình

Không chạy lại migration, tạo khóa hoặc FULL sync mỗi lần bật hệ thống. Với cách chạy local:

| Thứ tự | Thao tác / lệnh |
| --- | --- |
| 1 | Mở Docker Desktop; kiểm tra Mongo bằng `Get-Service MongoDB`. Nếu service đang dừng, chạy `Start-Service MongoDB` trong PowerShell Administrator. |
| 2 | Bật MASTER: `docker compose --env-file .env -f deploy/compose.master-only.yml up -d master-postgres` |
| 3 | Trong mỗi terminal Python: `.\.venv\Scripts\Activate.ps1` |
| 4 | Chạy API, frontend và các worker cần dùng theo bảng tại bước 5; mỗi tiến trình một terminal. |
| 5 | Mở <http://127.0.0.1:3000/login>; kiểm tra <http://127.0.0.1:8000/health>. |

Nếu chạy ứng dụng Docker, dùng bảng Docker ở bước 5 thay cho các tiến trình local. Không bật hai bộ worker cùng xử lý một hệ thống.

## Kiến trúc

| Thành phần | Nơi chạy | Vai trò |
| --- | --- | --- |
| PostgreSQL SOURCE | Neon | Metadata nguồn, phiên bản, OCR jobs/page tasks và content units |
| PostgreSQL MASTER | **Một container Docker local** | Metadata, mapping, read-model và trạng thái; migration `0020` |
| MongoDB | Windows native, database `etl_content` | Đúng 5 collection: `users`, `learning_resources`, `ocr_pages`, `mongo_content_units`, `content_bags` |
| Tệp gốc | Object Storage / Google Drive | Truy xuất theo quyền của nguồn |
| API, giao diện, worker | Windows host | Đồng bộ, xử lý và phục vụ người dùng |
| Neo4j (tùy chọn) | Container Docker riêng | Graph quan hệ được dựng từ MASTER, phục vụ học liệu liên quan |

Trong nhóm database PostgreSQL/Mongo, Docker **chỉ chạy PostgreSQL MASTER**. SOURCE dùng Neon hiện hữu, MongoDB chạy native trên Windows; không tạo SOURCE hoặc Mongo container. Neo4j và các container ứng dụng chỉ khởi động khi chọn dùng.

## 1. Chuẩn bị

Cần Python 3.12, Node.js 22.12+ (khuyến nghị 24), Docker Desktop, Git và MongoDB Community chạy native. Cài Tesseract kèm `vie`/`eng`; thêm LibreOffice cho tệp Office, FFmpeg/ffprobe cho media. Kiểm tra Docker đang dùng Linux containers.

Chạy PowerShell từ gốc repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev,embeddings,google-drive,media,translation]'
python -m pip check
npm.cmd --prefix frontend ci
```

Extra `embeddings` cài thư viện model và các dependency tương ứng; chọn bản PyTorch CPU/CUDA phù hợp môi trường nếu triển khai có yêu cầu riêng. Không dùng media có thể cài `.[dev,embeddings,google-drive]`. Dùng Neo4j thì cài thêm `python -m pip install -e '.[graph]'`. Các lệnh `python` dưới đây chạy trong venv đã kích hoạt. Dùng `npm.cmd` để tránh execution policy của PowerShell.

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
MONGO_URI=mongodb://127.0.0.1:27017/
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

| Nhóm cấu hình | Yêu cầu trước khi chạy |
| --- | --- |
| SOURCE reader | Neon URI riêng tư, DNS/TLS/CA hợp lệ; registry `postgresql` và namespace khớp nguồn |
| SOURCE writer / storage | Credential có quyền ghi OCR và truy cập tệp theo contract hiện hữu; reader không thay thế writer |
| MASTER | Host `127.0.0.1`, port `5434`, schema revision `0020` |
| Mongo | `MONGO_DATABASE=etl_content`; host dùng `127.0.0.1:27017`, container dùng `host.docker.internal:27017` |
| Đăng nhập | `AUTH_MODE=master_users` cho tài khoản SOURCE đã đồng bộ; `AUTH_ORIGIN` khớp địa chỉ UI đang dùng |
| OCR / index | Model, công cụ xử lý, owner contract, mapping service actor và các cờ tại bước 6 |

Không dùng tiền tố `VITE_` cho secret backend. `.env` phục vụ chạy host; `docker.env` là cấu hình container được chuẩn bị ở bước 5. Sau khi đổi cấu hình cần khởi động lại tiến trình liên quan.

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

## 4. Xác thực, model và MongoDB

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

MongoDB cần đúng 5 collection trong bảng kiến trúc, validator `strict/error`, indexes theo contract và embedding 1024 chiều. Kiểm tra schema hiện có:

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

### A. Chạy local: frontend, backend và các worker

Các tiến trình dưới đây chạy trực tiếp trên Windows, **không cần container ứng dụng**. Cần cài dependencies, chuẩn bị model/khóa và cấu hình `.env` ở các bước trên. Database MASTER phải đang truy cập được qua `DATABASE_URL` (ở mô hình hiện tại, chỉ MASTER chạy Docker); SOURCE Neon và MongoDB native phải sẵn sàng.

Mở **6 terminal** cho API, frontend và 4 worker xử lý thông thường; thêm 1 terminal khi dùng `master_users`, thêm 1 terminal khi đồng bộ Neo4j. Trong mỗi terminal Python chạy `.\.venv\Scripts\Activate.ps1`, sau đó chạy một lệnh trong bảng; giữ terminal mở. Lệnh frontend chạy bằng Node, không phụ thuộc venv.

| Tiến trình | Vai trò / khi cần bật | Lệnh khởi động local |
| --- | --- | --- |
| API | Backend, xác thực, tìm kiếm và điều phối yêu cầu | `python -m uvicorn data_sync_etl.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| React/Vite | Giao diện phát triển tại cổng 3000 | `npm.cmd --prefix frontend run dev` |
| Worker metadata | Nhận và thực thi job đồng bộ metadata | `python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1` |
| Worker tệp SOURCE | Xác minh metadata/tham chiếu nguồn; không import byte gốc | `python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1` |
| Worker OCR | Trích xuất/OCR/ASR và hoàn thiện chỉ mục; SOURCE remote chỉ qua capability RAM được cấp | `python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1` |
| Worker reconciliation | Chạy đối soát theo lịch đã lưu, đồng bộ đúng ID lệch | `python -m data_sync_etl.workers.reconciliation` |
| Worker users-refresh | Cần khi `AUTH_MODE=master_users`, giữ danh tính MASTER còn hiệu lực | `python -m data_sync_etl.cli run-users-refresh-worker --source-id postgresql` |
| Worker graph | Tùy chọn, cập nhật graph Neo4j từ MASTER | `python -m data_sync_etl.cli run-graph-worker` |

Nếu dùng Neo4j, mở thêm một terminal riêng cho graph worker; cần Neo4j đang chạy, dependencies `.[graph]` đã cài và `.env` có `NEO4J_ENABLED=true`, `NEO4J_SYNC_ENABLED=true` cùng cấu hình kết nối hợp lệ. Worker tự đối soát định kỳ theo `NEO4J_SYNC_INTERVAL_SECONDS` (mặc định 300 giây), độc lập với chế độ Search Shadow/Active. Xem [hướng dẫn Neo4j](deploy/NEO4J_ACTIVE_OPERATIONS.md#host-graph-operations).

Kiểm tra frontend từ gốc repository: `npm.cmd --prefix frontend run build` và `npm.cmd --prefix frontend run lint`. Vite cổng 3000 proxy API tới backend cổng 8000; đăng nhập tại `http://127.0.0.1:3000/login`.

Chỉ đọc/tìm kiếm dữ liệu có sẵn vẫn cần API, frontend và users-refresh nếu dùng `master_users`. Muốn tiếp nhận và xử lý học liệu mới thì bật đủ 4 worker thông thường. Muốn graph tự cập nhật thì bật graph worker. Chạy worker không tự tạo lịch Auto Sync hoặc cấp quyền OCR.

Bật lịch Auto Sync trên giao diện nếu cần đồng bộ định kỳ. Nhấn `Ctrl+C` trong từng terminal để dừng. Sau khi đổi `.env`, khởi động lại các tiến trình liên quan bằng các lệnh tương ứng ở trên.

- Giao diện: <http://127.0.0.1:3000>
- Master Data: <http://127.0.0.1:3000/systems/master-data>
- API health: <http://127.0.0.1:8000/health>

### B. Chạy ứng dụng bằng Docker

Với cấu hình local đã hoạt động và đủ model/contract được phê duyệt, chuẩn bị cấu hình container từ `.env`:

```powershell
python deploy/scripts/prepare-project-docker.py
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build -EnvFile docker.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -EnvFile docker.env -Workers
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status -EnvFile docker.env
```

Mở <http://127.0.0.1:8080>. Profile `neon-native-mongo` dùng MASTER đang chạy trên network `etl-master-only_default`, SOURCE Neon và MongoDB Windows native; không khởi tạo database hay migration. Host dùng `127.0.0.1:27017`, container truy cập Mongo qua `host.docker.internal:27017`. `docker.env` giữ cấu hình container, còn credential/contract được mount từ `ETL_Secrets/`; model mount chỉ đọc từ `Models/`. Script chuẩn bị giữ phạm vi quyền trong `.env` và contract hiện có, không tự cấp quyền. Khi đổi `.env`, chạy lại bước chuẩn bị và Start; khi đổi code, Build rồi Start. Tránh chạy đồng thời worker local khi đã chuyển sang Docker.

Nếu dùng Neo4j và `master_users`, khởi động Neo4j theo bước 7 trước rồi dùng bộ lệnh có `-GraphServices` bên dưới. Switch này bật **cả users-refresh-worker và graph-worker**; không phải switch bật 4 worker xử lý. Giữ cùng các switch khi kiểm tra/dừng để launcher nhìn thấy đúng nhóm container.

| Mục đích | Lệnh PowerShell từ gốc repository |
| --- | --- |
| Sinh cấu hình container từ `.env` | `python deploy/scripts/prepare-project-docker.py` |
| Build ứng dụng và 2 worker graph/auth | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build -EnvFile docker.env -GraphServices` |
| Preflight auth, model và kết nối DB | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Check -EnvFile docker.env -GraphServices` |
| Bật API/frontend, 4 worker thường và 2 worker graph/auth | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -EnvFile docker.env -Workers -GraphServices` |
| Xem trạng thái | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status -EnvFile docker.env -GraphServices` |
| Xem 100 dòng log gần nhất | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Logs -EnvFile docker.env -GraphServices` |
| Dừng ứng dụng và worker, giữ database | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Stop -EnvFile docker.env -GraphServices` |

Không dùng Neo4j thì bỏ `-GraphServices`; users-refresh có thể chạy bằng lệnh local trong bảng A khi API dùng `master_users`. Không chạy thêm users-refresh local nếu container tương ứng đã bật. `Start` có kiểm tra trước và không tự build; sau khi đổi code cần `Build` rồi `Start`. Nếu `.env` ghim image bằng `BACKEND_IMAGE`, `FRONTEND_IMAGE` hoặc `GRAPH_SERVICES_IMAGE`, xác minh các tag đó chính là image vừa build.

Mongo native phải truy cập được từ container qua host gateway. Nếu preflight thất bại, kiểm tra service, bind address và Windows Firewall theo môi trường; không mở Mongo ra Internet hoặc vô hiệu hóa xác thực để chữa lỗi kết nối.

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

## 7. Neo4j và graph worker (tùy chọn)

Neo4j là graph projection từ MASTER, không thay SOURCE, ContentBag hay kiểm tra quyền. Chuẩn bị dependency `.[graph]`, secret và cấu hình theo [runbook Neo4j](deploy/NEO4J_ACTIVE_OPERATIONS.md#host-graph-operations). Giữ nguyên secret, ownership receipt và state directory nếu graph đã có dữ liệu.

| Thao tác | Lệnh |
| --- | --- |
| Kiểm tra cấu hình | `python deploy/scripts/check-neo4j-config.py --env-file .env` |
| Bật database graph | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-graph.ps1 -Action GraphStart -EnvFile .env` |
| Trạng thái container | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-graph.ps1 -Action GraphStatus -EnvFile .env` |
| Kiểm tra kết nối / ownership | `python -m data_sync_etl.cli graph-preflight` |
| Đối chiếu graph với MASTER | `python -m data_sync_etl.cli graph-diff` |
| Xem trạng thái graph | `python -m data_sync_etl.cli graph-status` |
| Bật đồng bộ tự động trên host | `python -m data_sync_etl.cli run-graph-worker` |
| Dừng database graph | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-graph.ps1 -Action GraphStop -EnvFile .env` |

Worker cần `NEO4J_ENABLED=true`, `NEO4J_SYNC_ENABLED=true`, kết nối hợp lệ và ownership đã xác minh. Chu kỳ mặc định 300 giây qua `NEO4J_SYNC_INTERVAL_SECONDS`. Chỉ chạy một graph writer; chọn host worker hoặc container graph-worker. Trước khi dừng Neo4j, dừng graph worker tương ứng.

Neo4j Browser: <http://127.0.0.1:7474>, Bolt host: `bolt://127.0.0.1:7687`. Tài khoản Neo4j dùng secret graph, tách biệt đăng nhập MASTER. Template mặc định tắt tính năng; `NEO4J_RELATED_MODE` điều khiển “Học liệu liên quan”, độc lập với `NEO4J_SEARCH_MODE`. Chỉ bật Active theo gate nghiệm thu trong runbook; khởi động database không đồng nghĩa đã bật Active.

Với graph mới/trống, làm theo [contract Light Node](deploy/NEO4J_ACTIVE_OPERATIONS.md#light-node-contract) để chuẩn bị ownership và backfill. Không dùng `--claim-empty` hoặc chạy lại FULL apply lên graph hiện hữu như một bước khởi động thường ngày.

## 8. Kiểm tra sau khởi động và dừng hệ thống

| Kiểm tra | Kết quả cần thấy |
| --- | --- |
| MASTER | Compose báo healthy; `python -m alembic current` trả revision `0020` |
| Mongo | Service Running; 5 collection đúng contract; container preflight đọc được dữ liệu |
| API | `Invoke-RestMethod http://127.0.0.1:8000/health` khi chạy host; Docker kiểm tra qua `http://127.0.0.1:8080/health` |
| Đăng nhập | Dùng tài khoản MASTER hiện hành được cấp phép, không có mật khẩu mặc định |
| Đồng bộ | Lịch đã bật hoặc job đã được yêu cầu; worker metadata không lỗi kết nối/quyền |
| OCR/index | Học liệu đủ điều kiện tiến triển đến nội dung và chỉ mục READY; DRAFT vẫn riêng tư |
| Tìm kiếm | Thử BoW, Vector, Hybrid; Vector/Hybrid cần model đã sẵn sàng và ContentBag hợp lệ |
| Neo4j | Graph worker hoạt động, `graph-diff` không báo lệch sau đồng bộ; UI liên quan phụ thuộc mode/ACL |

Khi chạy local, dừng từng worker bằng `Ctrl+C`, sau đó frontend/API. Khi chạy Docker, dùng action `Stop` với đúng switch trong bảng B. Dừng graph worker trước `GraphStop`; dừng MASTER bằng lệnh `stop master-postgres` ở bước 3 nếu muốn tắt toàn bộ. Mongo native là service riêng, không bị launcher Docker dừng.

Để bật lại, dùng đúng lệnh khởi động ban đầu. Không cần xóa database, volume, tạo lại OCR hoặc chạy migration. Kiểm tra health chỉ chứng minh tiến trình đáp ứng; cần kiểm tra đăng nhập và các luồng chức năng để xác nhận hệ thống sẵn sàng.

## 9. Chuyển máy và xử lý lỗi

Mang theo mã nguồn, `.env`, `ETL_Secrets/`, `Models/`; cài lại dependencies và công cụ hệ thống, chỉnh đường dẫn theo máy mới. Muốn tiếp tục dữ liệu hiện tại cần backup/restore MASTER, Mongo native và `storage/`; thêm graph/state nếu dùng Neo4j. Không chỉ sao chép code rồi kỳ vọng có OCR/ContentBag cũ. MASTER mới cần migration `0020`, đồng bộ lại và xác minh mapping/provenance trước khi reuse OCR cũ. Xem [backup và restore database](deploy/DATABASE_SETUP.md).

| Hiện tượng | Kiểm tra |
| --- | --- |
| Không kết nối MASTER | Container healthy, cổng 5434, user/database/password khớp `.env` |
| Lỗi kết nối nguồn/nội dung | Neon: credential, DNS/TLS/CA và quyền; Mongo native: service, port 27017 và host gateway khi chạy Docker |
| `AUTH_MASTER_STALE` | FULL users và worker users-refresh |
| Chờ quyền xử lý | Namespace/lifecycle/actor trong owner contract và quyền ứng dụng |
| Model chưa READY | Đường dẫn, revision, manifest; chờ preload trước Vector/Hybrid |
| Không đọc/xử lý được tệp | Mapping, hash, MIME, quyền storage và provenance |
| Cổng 3000/8000 đang được dùng | Kiểm tra tiến trình cũ; dừng bộ local trước khi chuyển cách chạy, không bật API trùng |
| Đồng bộ không tự chạy | Kiểm tra lịch Auto Sync và worker metadata/reconciliation; chạy worker không tự bật lịch |
| Docker đang chạy code/cấu hình cũ | Chuẩn bị lại `docker.env`, kiểm tra image tag, Build rồi Start với đúng switch |
| Worker restart với `SOURCE_NEON_ATLAS_TLS_REQUIRED` sau khi chuyển Mongo local | Container còn profile Atlas cũ. Chạy lại launcher `-Action Start -EnvFile docker.env -Workers -GraphServices` theo bảng B để Compose tạo lại container đúng cấu hình; nút Start/Restart trong Docker Desktop không cập nhật environment/image của container cũ. Nếu không dùng graph, bỏ `-GraphServices`. |
| Graph không cập nhật / UI không có liên quan | Worker, flags sync/related, ownership, graph-diff và ACL; Search Shadow không tự bật Related Active |

Dùng CLI và worker trong mã nguồn để vận hành; các script chẩn đoán một lần trong `.local/` không phải thành phần khởi động.


Tài liệu chuyên sâu: [database](deploy/DATABASE_SETUP.md), [xác thực MASTER](deploy/AUTH_MASTER_USERS_SETUP.md), [Docker](deploy/README.md), [Google Drive](deploy/DRIVE_SETUP.md), [Neo4j / backup / rollback](deploy/NEO4J_ACTIVE_OPERATIONS.md).

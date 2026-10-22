# ETL — Kho học liệu số

Đăng nhập đồng thời user MASTER và admin riêng: [hướng dẫn master_users](deploy/AUTH_MASTER_USERS_SETUP.md).

Đồng bộ metadata SOURCE → Master, OCR/trích xuất → ContentUnit/ContentBag → tìm kiếm BoW/Vector/Hybrid. Hỗ trợ PDF, DOCX, PPTX, XLSX, CSV, TXT, PNG/JPG/JPEG. Word/PPT xem dạng PDF, bảng tính xem dạng lưới; tải xuống giữ định dạng gốc.

**SOURCE chỉ lưu tham chiếu**, đọc Local/Drive khi xử lý, xem hoặc tải; chỉ dùng file tạm theo tác vụ. File **LOCAL do Master sở hữu** vẫn lưu trong `STORAGE_ROOT`.

Các lệnh chạy từ **gốc repo trong Git Bash trên Windows**. Có thể giữ nguyên tên/cổng/đường dẫn mẫu; chỉ thay email, mật khẩu và thông tin nguồn của bạn. Không có tài khoản/mật khẩu demo mặc định.

## 1. Chạy local

API, React và ba worker chạy trên host; Docker chỉ chạy database. Cần Git, **Python 3.12**, **Node.js 22.12+**, Docker Desktop (Linux containers), Tesseract `vie+eng` và LibreOffice để OCR ảnh/xem trước Office.

### 1.1. Cài dependencies

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,embeddings,google-drive]"
npm.cmd --prefix frontend ci
test -f .env || cp .env.example .env
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/storage /d/etl/models/minilm
```

Chỉ dùng Local thì có thể bỏ extra `google-drive`. Linux: dùng `source .venv/bin/activate`, `npm` thay `npm.cmd` và đổi `D:/etl/...` thành đường dẫn tuyệt đối của máy. `.env` không hỗ trợ đường dẫn `~` như shell.

### 1.2. Sửa `.env` ở gốc repo

Giữ các nhóm còn lại theo [.env.example](.env.example); cập nhật key đã có, tránh trùng. Thay ba `YOUR_*_PASSWORD` bằng mật khẩu riêng; ký tự đặc biệt trong URI phải percent-encode. File này không commit.

```dotenv
APP_ENVIRONMENT=development
ADMIN_ENABLED=true
POSTGRES_USER=etl
POSTGRES_DB=etl
POSTGRES_PASSWORD=YOUR_MASTER_PASSWORD
DATABASE_URL=postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@127.0.0.1:5432/etl
SOURCE_ADAPTER=postgresql
SOURCE_POSTGRES_USER=etl_source_admin
SOURCE_POSTGRES_DB=etl_source
SOURCE_POSTGRES_PASSWORD=YOUR_SOURCE_OWNER_PASSWORD
SOURCE_MIGRATION_DATABASE_URL=postgresql+psycopg://etl_source_admin:YOUR_SOURCE_OWNER_PASSWORD@127.0.0.1:5433/etl_source
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:YOUR_SOURCE_READER_PASSWORD@127.0.0.1:5433/etl_source
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
SEARCH_INTERNAL_DEV_ENABLED=false
INDEX_AUTO_ENABLED=false
```

| Cấu hình | Ý nghĩa/cách điền |
|---|---|
| `DATABASE_URL` | Master; role được ghi dữ liệu ứng dụng. |
| `SOURCE_DATABASE_URL` | SOURCE; **reader chỉ SELECT**, không owner/superuser. |
| `SOURCE_MIGRATION_DATABASE_URL` | Owner chỉ dùng khi chủ DB duyệt khởi tạo/migration; không truyền vào Docker app. |
| `SOURCE_FILE_STORAGE_ROOT` | Kho nguồn có sẵn; đọc `<root>/<storage_bucket>/<storage_object_key>`, không tự quét folder tạo học liệu. |
| `STORAGE_ROOT`, `SOURCE_TEMP_ROOT` | Kho LOCAL và thư mục tạm; ba thư mục này và kho nguồn không trùng/lồng nhau. |
| Hai cờ SOURCE reference/temp | Cho phép đọc nguồn/file tạm; không sao chép SOURCE lâu dài. |

DB có sẵn: dùng thông tin thực do chủ DB cấp và **bỏ bước khởi tạo**. Đổi `.env` không đổi mật khẩu role đã tồn tại.

### 1.3. Database mới, trống — khởi tạo một lần

[docker-compose.yml](docker-compose.yml) ở gốc có migration/bootstrap: chỉ chạy khi xác nhận DB local mới của bạn, không trỏ vào Dev dùng chung/Production.

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
python -m data_sync_etl.adapters.mongo.schema --check
```

Ba job khởi tạo phải kết thúc mã 0; Mongo cần `schema_ready=true`, embedding 384D. DB trống chưa có học liệu mẫu. Tạo SOURCE reader và đặt mật khẩu khớp `.env`:

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 \
  -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong `psql`: chạy `\password etl_source_reader`, nhập mật khẩu reader rồi `\q`. Nếu đổi tên DB/user mẫu, sửa cả lệnh và `GRANT CONNECT` trong SQL.

### 1.4. Đăng nhập local

Làm lần lượt các bước dưới đây sau khi cài dependencies và cấu hình DB. Nếu chỉ cần chạy thử với DB trống, chọn `single_admin`; nếu muốn dùng cả user đang có trong MASTER và admin riêng, chọn `master_users` ở bước4.

**Bước1 — Chọn chế độ đăng nhập.**

Chọn **một** chế độ cho `AUTH_MODE`; form đăng nhập chỉ có email/mật khẩu, không chọn loại tài khoản:

| Chế độ | Tài khoản dùng để đăng nhập | Khi nào dùng |
|---|---|---|
| `single_admin` | Một admin với email cấu hình và mật khẩu do bạn tạo bên dưới | Cài mới, chưa có người dùng MASTER. |
| `master_users` | User MASTER và admin riêng nếu `ADMIN_ENABLED=true` | Đã có người dùng được đồng bộ SOURCE → MASTER. Không cần Auth DB mới. |
| `db_users` | Tài khoản và RBAC trong PostgreSQL Auth DB riêng | Cần quản lý tài khoản độc lập; làm theo [Auth Database setup](deploy/AUTH_DATABASE_SETUP.md). |

**Bước2 — Tạo secret và đặt mật khẩu admin.** Kích hoạt `.venv`, chạy từ gốc repo. Nếu đã có đủ ba file secret thì dùng lại, không chạy `init` lần nữa:

```bash
python -m data_sync_etl.security.cli init --directory D:/etl/private/auth
```

Nhập/xác nhận mật khẩu **ít nhất 15 ký tự**; terminal không hiện ký tự. Ghi nhớ mật khẩu bạn vừa nhập để đăng nhập. CLI tạo hash Argon2id và hai khóa, không ghi đè file đã có. Kết quả mong đợi: `PASS: auth secret files created; password not displayed.` và có ba file sau:

```text
D:/etl/private/auth/
├── admin_password_hash
├── session_signing_key
└── auth_proxy_key
```

**Bước3 — Điền cấu hình admin vào `.env`.** Mở file tại **gốc repo**:

```bash
notepad.exe .env
```

Cập nhật các key đã có hoặc thêm key còn thiếu, không chép thêm dòng trùng. Sao chép khối dưới đây, thay `YOUR_EMAIL` bằng email admin bạn muốn dùng. Các đường dẫn giữ nguyên nếu đã tạo secret đúng bước2:

```dotenv
AUTH_MODE=single_admin
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:3000
ADMIN_LOGIN_EMAIL=YOUR_EMAIL
ADMIN_PASSWORD_HASH_FILE=D:/etl/private/auth/admin_password_hash
SESSION_SIGNING_KEY_FILE=D:/etl/private/auth/session_signing_key
AUTH_PROXY_KEY_FILE=D:/etl/private/auth/auth_proxy_key
AUTH_STATE_PATH=D:/etl/private/auth/sessions.sqlite3
```

| Giá trị | Lấy ở đâu / cách nhập |
|---|---|
| `ADMIN_LOGIN_EMAIL` | Email admin do bạn chọn, ví dụ `admin@example.com`; thay `YOUR_EMAIL`, không nhập mật khẩu ở đây. Không cần có bản ghi MASTER tương ứng. |
| Mật khẩu admin | Chính mật khẩu nhập hai lần khi chạy CLI; không có mật khẩu mặc định. CLI chỉ lưu hash, không lưu bản gốc để xem lại. |
| `ADMIN_PASSWORD_HASH_FILE` | File `admin_password_hash` vừa tạo, chứa hash Argon2id. Đây là đường dẫn file, không phải nội dung hash. |
| `SESSION_SIGNING_KEY_FILE` | File khóa ký session do CLI tạo; giữ ổn định giữa các lần chạy. |
| `AUTH_PROXY_KEY_FILE` | File khóa giữa Vite/reverse proxy và Backend do CLI tạo; không nhập vào form login. |
| `AUTH_STATE_PATH` | File SQLite session riêng; thư mục cha phải ghi được. Không dùng DB nghiệp vụ để lưu phiên. |
| `AUTH_ORIGIN` | Địa chỉ trình duyệt thực sự mở: local mặc định `http://127.0.0.1:3000`. Đổi cổng/hostname thì sửa tương ứng. |

`ADMIN_ENABLED=true` bật tính năng quản trị, vẫn yêu cầu đăng nhập. Trong `master_users`, `false` chặn admin riêng; các route thuộc LocalAdminBoundary vẫn có thể trả403 khi cờ tắt. Vite đọc proxy key từ Backend `.env` ở phía server; không đưa secret vào `VITE_*`. `AUTH_COOKIE_SECURE=false` chỉ dành cho HTTP localhost trong ví dụ này; LAN/Production cần HTTPS và cấu hình TLS phù hợp.

**Bước4 — Bật tài khoản MASTER nếu cần.** Nếu chỉ dùng admin thì bỏ qua bước này. Nếu dùng cả hai, giữ nguyên email/secret ở bước3, **đổi dòng `AUTH_MODE` hiện có** và thêm các key MASTER trong **`.env` gốc repo**:

```dotenv
AUTH_MODE=master_users
ADMIN_ENABLED=true
AUTH_MASTER_FRESHNESS_SECONDS=120
AUTH_MASTER_PBKDF2_SALT_ENCODING=auto
AUTH_MASTER_RESOURCE_GRANTS={}
```

- User nhập email và mật khẩu hiện có ở SOURCE, sau khi đồng bộ sang MASTER. Không dùng mật khẩu PostgreSQL hoặc mật khẩu admin riêng. DB mới, trống chưa có user để đăng nhập; cần dữ liệu SOURCE và worker đồng bộ users thành công.
- Backend đọc `master_users` qua `DATABASE_URL` bằng transaction READ ONLY; không cần thêm URL Auth DB. User phải active, email verified, role active, chưa deleted/source_missing, có nguồn POSTGRESQL và email chuẩn hóa không trùng.
- Trong120 giây gần nhất phải có users sync_run COMPLETED; chạy Master Sync Worker ở mục1.6. Nếu dữ liệu cũ, Backend từ chối đăng nhập/session MASTER. Admin riêng vẫn dùng được khi cờ bật và secret hợp lệ.
- **Quyền MASTER:** tài khoản có `role_code=ADMIN`, account/role active, email verified và dữ liệu sync còn mới nhận ADMIN khi `ADMIN_ENABLED=true`. Backend cấp quyền Sync/Process/Index/Evaluation và giao diện hiện các chức năng quản trị. Tắt cờ thì không cấp ADMIN; thay đổi role/status làm phiên cũ hết hiệu lực. Các role khác nhận Viewer. `/systems/users` chỉ dành cho Auth DB (`db_users`); tài khoản MASTER được quản lý tại SOURCE.
- **Kho cá nhân của Viewer:** chỉ gồm học liệu PUBLISHED có `created_by` đúng ID MASTER của mình. Chủ sở hữu được tìm kiếm BoW/Vector/Hybrid, xem ngữ cảnh và xem/tải tệp đã xử lý, có chỉ mục READY, kể cả trạng thái mặc định `UNREVIEWED` + `retrieval_eligible=false`. Ngoại lệ này chỉ cho chủ sở hữu đang đăng nhập; người khác không tìm/đọc được nội dung chưa duyệt đó. Nội dung REJECTED, bị thu hồi hoặc có hạn chế vẫn bị chặn. `AUTH_MASTER_RESOURCE_GRANTS` là cấu hình cũ, không mở rộng kho cá nhân. Viewer không được Sync/Index/Admin; các nút không có quyền được ẩn. Admin vẫn quản lý toàn bộ kho theo quyền được cấp.
- **Tìm kiếm và xem tệp public:** user đã đăng nhập (`master_users` hoặc `db_users`) được tra cứu BoW/Vector/Hybrid và xem ngữ cảnh ngoài kho cá nhân. Public trong hợp đồng hiện tại là tài liệu PUBLISHED đang hoạt động, phiên bản hợp lệ, chỉ mục hiện hành sẵn sàng, ContentUnit APPROVED/REVIEWED và `retrieval_eligible=true`; mọi cờ hạn chế quyền/riêng tư/giấy phép vẫn chặn. Nếu **toàn bộ nội dung tệp** đủ điều kiện, user được mở chi tiết, xem/tải và mở link nguồn được phép; danh sách phiên bản chỉ trả phiên bản đủ quyền. Nếu chỉ một phần nội dung đủ quyền, kết quả vẫn có Ngữ cảnh nhưng ẩn link mở toàn tệp (`can_view_file=false`). Không mở cho khách chưa đăng nhập và không cấp quyền sửa/xử lý/quản trị. Không cần thêm cờ ENV theo từng tệp; nguồn vẫn kiểm tra quyền và hash khi truy xuất.
- Nếu tìm kiếm trả danh sách rỗng: kiểm tra từ khóa, tài liệu đã CONTENT_READY/chỉ mục READY và `created_by` đúng ID MASTER của tài khoản. Unit UNREVIEWED mặc định chỉ tìm được bởi chủ sở hữu; tài liệu người khác phải có nội dung APPROVED/REVIEWED và retrieval_eligible=true. Không cần chỉnh ENV theo từng tài liệu và không tự đổi trạng thái duyệt để mở kết quả public.
- `auto` xác minh PBKDF2 theo cả salt text và hex của định dạng được hỗ trợ; không đổi hash trong DB. Nếu trùng email với admin, chỉ mật khẩu admin khớp secret mới cấp admin; mật khẩu MASTER khác vẫn là Viewer.

**Bước5 — Khởi động và thử đăng nhập.** Hoàn tất model/OCR ở mục1.5 rồi chạy Backend, Frontend và các worker theo mục1.6. Nếu các tiến trình đang chạy thì restart Backend; đổi proxy key thì restart cả Vite. Mở **http://127.0.0.1:3000/login**:

| Muốn đăng nhập | Nhập email | Nhập mật khẩu |
|---|---|---|
| Admin riêng | Giá trị `ADMIN_LOGIN_EMAIL` ở bước3 | Mật khẩu đã nhập ở bước2, không phải nội dung file hash. |
| Người dùng MASTER | Email tài khoản đã đồng bộ từ SOURCE | Mật khẩu của chính tài khoản đó tại SOURCE. |

Form không có dropdown. Admin riêng cần `ADMIN_ENABLED=true`; user MASTER cần `AUTH_MODE=master_users`. Không nhập mật khẩu DB PostgreSQL vào form. Hướng dẫn quyền và đồng bộ: [MASTER users](deploy/AUTH_MASTER_USERS_SETUP.md).

Kiểm tra cấu hình công khai qua Frontend sau khi chạy:

```bash
curl -s http://127.0.0.1:3000/api/v1/auth/config
```

Với chế độ MASTER + admin ở trên, kết quả phải có `"mode":"master_users"`, `"login_required":true`, `"admin_feature_enabled":true`. Nếu vẫn hiện mode cũ, kiểm tra đúng file `.env` và restart Backend.

**Nhiều tài khoản/RBAC:** có thể chọn `AUTH_MODE=db_users` với PostgreSQL Auth DB riêng, giữ nguyên SOURCE/Master/Mongo. Làm theo [Auth Database setup](deploy/AUTH_DATABASE_SETUP.md) để provision, migration riêng, bootstrap admin và chuyển mode sau kiểm thử; không tự đồng bộ tài khoản SOURCE thành admin. `single_admin` vẫn là mặc định/rollback có kiểm soát.

### 1.5. OCR, Office và model — chuẩn bị một lần

Cài [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) có English/Vietnamese và LibreOffice. Kiểm tra đường dẫn cài thực tế rồi sửa `.env`:

```dotenv
OCR_ENGINE=tesseract
TESSERACT_PATH="C:/Program Files/Tesseract-OCR/tesseract.exe"
TESSERACT_LANGUAGES=vie+eng
OFFICE_PDF_EXECUTABLE="C:/Program Files/LibreOffice/program/soffice.exe"
INDEX_EMBEDDING_MODEL_PATH=D:/etl/models/minilm
INDEX_EMBEDDING_MODEL_ID=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
INDEX_EMBEDDING_MODEL_VERSION=e8f8c211226b894fcb81acc59f3b34ba3efd5f42
INDEX_EMBEDDING_DIMENSION=384
```

```bash
"/c/Program Files/Tesseract-OCR/tesseract.exe" --list-langs
# prepare cần Internet và chấp nhận license; bỏ qua nếu đã có đúng model.
python deploy/runtime/model.py prepare --directory D:/etl/models/minilm \
  --manifest D:/etl/models/minilm/etl-model-manifest.json --accept-model-license
python deploy/runtime/model.py encode --directory D:/etl/models/minilm \
  --manifest D:/etl/models/minilm/etl-model-manifest.json
```

Danh sách OCR cần `vie`/`eng`; `encode` kiểm tra offline/384D. Đã có model/revision nhưng thiếu JSON manifest: dùng action `manifest` thay `prepare`, rồi `encode`, không tải lại. Model/token không đi kèm khi pull code. Linux cài các gói `tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie libreoffice-writer libreoffice-impress`, chỉnh executable theo máy.

#### Chọn BGE-M3 thay MiniLM

BGE-M3 dùng **dense vector1024 chiều**; MiniLM dùng384 chiều. Adapter hiện tại dùng dense embedding, chưa dùng sparse/ColBERT của BGE-M3. Chỉ đổi đường dẫn là chưa đủ: ID, revision, dimension và manifest phải khớp nhau.

**Local:** sửa các key hiện có trong `.env` gốc repo, ví dụ dùng thư mục `Models/bge-m3` trong repo:

```dotenv
INDEX_EMBEDDING_MODEL_PATH=./Models/bge-m3
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
INDEX_AUTO_ENABLED=false
SEARCH_MODEL_WARMUP_TIMEOUT_SECONDS=120
```

Nếu chưa tải model, lệnh sau tải đúng revision và tạo cả `etl-model-revision.txt`, `etl-model-manifest.json` chứa checksum. Cần Internet/license; chỉ chuẩn bị một lần. Dùng Python trực tiếp nếu `.venv` đã chuyển đường dẫn và launcher `hf.exe` bị lỗi:

```bash
./.venv/Scripts/python.exe deploy/runtime/model.py prepare \
  --directory ./Models/bge-m3 --manifest ./Models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --dimension 1024 --accept-model-license
```

Đã chuẩn bị đủ model/manifest thì chỉ kiểm tra offline:

```bash
./.venv/Scripts/python.exe deploy/runtime/model.py encode \
  --directory ./Models/bge-m3 --manifest ./Models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --dimension 1024
```

Kết quả cần có `"status": "PASS"` và `"dimension": 1024`. Công cụ model đọc tham số CLI hoặc biến môi trường tiến trình, không tự đọc `.env`, nên các lệnh trên truyền rõ model/revision/dimension. `.bin` được nạp qua Transformers/PyTorch đã cài; không bật remote code hoặc tắt kiểm tra an toàn để nạp model.

**Docker:** trong `D:/etl/private/app.env`, đổi các key hiện có (thay đường dẫn bằng vị trí thực tế trên máy):

```dotenv
MODEL_DIR=D:/StudyMaterials/HK7/Final_Project/ETL/Models/bge-m3
MODEL_MANIFEST_FILE=D:/StudyMaterials/HK7/Final_Project/ETL/Models/bge-m3/etl-model-manifest.json
INDEX_EMBEDDING_MODEL_ID=BAAI/bge-m3
INDEX_EMBEDDING_MODEL_VERSION=5617a9f61b028005a4858fdac845db406aefb181
INDEX_EMBEDDING_DIMENSION=1024
INDEX_AUTO_ENABLED=false
API_MEMORY_LIMIT=6g
OCR_MEMORY_LIMIT=6g
```

6g là mức cấu hình khởi đầu cho mỗi container có model, cần đo theo tải thực tế và RAM Docker; không phải kết quả benchmark. API và OCR worker nạp model riêng. Compose giữ tên mount trong container `/opt/models/minilm` để tương thích, nhưng nội dung mount được chọn bởi `MODEL_DIR` và contract trên.

Build lại Backend theo mục2.2 để có runtime mới. Nếu dùng model local đã chuẩn bị thì bỏ PrepareModel. Nếu chỉ có Docker, dùng lệnh PrepareModel của mục2.2 và thêm `-ModelId BAAI/bge-m3 -ModelRevision 5617a9f61b028005a4858fdac845db406aefb181 -ModelDimension 1024`, đổi `-ModelDirectory` sang thư mục BGE-M3. Linux: thêm `--model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024` sau `--accept-model-license` của action `prepare-model`.

**Chuyển dữ liệu trước khi sử dụng Search:** dừng các worker có thể ghi index theo quy trình vận hành; giữ `INDEX_AUTO_ENABLED=false`. Mongo validator và embedding cũ384 chiều không tương thích1024 chiều. Cần kế hoạch backup, cập nhật validator được chủ DB phê duyệt và xây lại embedding/ContentBag/chỉ mục bằng cùng model. Không cắt/nối vector cũ để đổi chiều và không xóa dữ liệu lịch sử. Đổi ENV không thực hiện migration hoặc reindex. Check có thể trả `MONGO_SCHEMA_NOT_READY` cho đến khi hoàn tất bước này. Chỉ restart/chuyển các dịch vụ sang BGE-M3 và bật lại auto indexing sau khi xác minh schema/index; query và index phải cùng ID/revision/dimension.

**Chuyển dữ liệu MiniLM local đã có sang BGE-M3:** công cụ [transition_embedding_model.py](scripts/transition_embedding_model.py) chỉ chấp nhận MASTER/MongoDB loopback và schema gốc384 hợp lệ. Đây là maintenance có ghi dữ liệu, không chạy tự động ở startup và không dùng cho Dev dùng chung/Production. Cần phê duyệt backup/validator/reindex trước khi chạy.

1. Chuẩn bị và kiểm tra model1024 theo lệnh trên. Dừng API/worker local và các worker Docker cùng dùng DB; đợi OCR/index đang chạy hoàn tất. Không để hai phiên bản model cùng ghi index.
2. Trong `.env`, giữ `INDEX_AUTO_ENABLED=false`, thêm `INDEX_HISTORICAL_EMBEDDING_DIMENSIONS=[384]`. Đặt cùng giá trị trong `app.env` khi Docker dùng database đã chuyển. Database BGE mới/trống không có lịch sử thì giữ `[]`.
3. Chạy kế hoạch chỉ đọc từ gốc repo:

```bash
./.venv/Scripts/python.exe scripts/transition_embedding_model.py
```

4. Sau khi kiểm tra số phiên bản/bag trong kế hoạch, chạy maintenance với **thư mục backup mới, ngoài Git**:

```bash
./.venv/Scripts/python.exe scripts/transition_embedding_model.py \
  --apply --confirm-local-maintenance \
  --backup-directory D:/etl/private/model-transition-backups/bge-run001
```

Công cụ sao lưu BSON của5 collection, validator gốc và lịch sử `etl_stage_runs`; kiểm tra checksum/đọc lại backup trước khi ghi. Mỗi ContentBag cũ được giữ thêm một bản lịch sử với ID mới, `is_current=false`, kèm `archive-map.json` trong backup để truy ngược ID gốc. Chỉ đổi validator embedding: vector hiện tại phải1024 chiều, vector384 chỉ được lưu khi inactive. Không thêm collection/field nghiệp vụ, không đổi SOURCE/PostgreSQL schema, không đọc lại file SOURCE hoặc chạy OCR. Reindex dùng ContentUnit hiện có, tạo INDEX_BUILD run mới; giữ nguyên run cũ. Báo cáo trong `transition-report.json`, kiểm tra cả BoW/Vector/Hybrid bằng pipeline tìm kiếm nội bộ có SearchAccess.

5. Chỉ khởi động lại dịch vụ khi báo cáo `COMPLETE`, không có run thất bại và schema hợp lệ. Docker cần build image có code/schema guard mới rồi Start với đúng overlays; không dùng image MiniLM cũ. Giữ `INDEX_AUTO_ENABLED=false` cho đến khi quyết định bật tự động sau nghiệm thu. Đăng nhập admin và thử tìm kiếm qua giao diện để xác minh phiên/quyền thực tế.

Công cụ chuyển một lần từ schema384; không chạy lại trên schema đã chuyển. Nếu dừng giữa chừng, giữ nguyên backup và `transition-report.json`, kiểm tra phase trước khi phục hồi; không xóa dữ liệu hoặc cố chạy lại toàn bộ. Backup chứa dữ liệu học liệu/tài khoản, giữ ngoài Git và giới hạn quyền truy cập.

**Rollback sau khi đã chuyển:** không chỉ sửa ENV về MiniLM vì validator và bag hiện tại đã đổi. Cần cửa sổ maintenance riêng để khôi phục validator/bag/index-run tương ứng từ backup, đồng thời giữ bản BGE mới. Không tự xóa model/vector hoặc restore đè database đang chạy.

### 1.6. Khởi động hằng ngày

DB đã khởi tạo: `docker compose up -d postgres source-postgres mongo` (không chạy lại migration). Mở **5 terminal** ở repo; mỗi terminal Python kích hoạt `.venv`:

| Tiến trình | Lệnh |
|---|---|
| Backend | `python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| Frontend | `npm.cmd --prefix frontend run dev` |
| Đồng bộ metadata | `etl run-master-sync-worker --worker-id master-sync-1` |
| File nguồn | `etl run-source-file-worker --worker-id source-file-1` |
| OCR/ContentUnit/ContentBag/Index | `etl run-ocr-worker --worker-id ocr-1` |

Mở **http://127.0.0.1:3000/login**. Dùng đúng `127.0.0.1` theo `AUTH_ORIGIN`; Backend cổng 8000, Vite proxy qua 3000. Sau khi đổi `.env`, restart API/worker; đổi proxy key thì restart cả Vite. Kiểm tra Database tại **Hệ thống → Trạng thái** sau đăng nhập; `/health` trong auth mode chỉ kiểm tra API.

**Swagger local:** đăng nhập bằng admin riêng tại3000, sau đó mở **http://127.0.0.1:3000/docs**. Vite proxy cả `/docs` và `/openapi.json` qua cơ chế xác thực hiện có. Không mở trực tiếp `http://127.0.0.1:8000/docs` trong auth mode: trang có thể tải nhưng schema bị403 vì thiếu proxy credential. User MASTER Viewer không được xem toàn bộ API definition. Nếu vừa cập nhật cấu hình Vite, restart `npm.cmd --prefix frontend run dev`.

Nguồn cần metadata, tham chiếu, SHA-256 và trạng thái `APPROVED` hợp lệ. Đồng bộ metadata → đợi `CONTENT_READY` → xây chỉ mục tới `READY` → tìm kiếm. Có thể bật `INDEX_AUTO_ENABLED=true` sau khi kiểm tra model/schema; không tự backfill lịch sử. Dừng terminal bằng Ctrl+C, DB bằng `docker compose stop`.

**Drive tùy chọn:** làm theo [Drive setup](deploy/DRIVE_SETUP.md); mỗi người tự có token và quyền Reader. Chỉ Local thì bỏ qua. Mặc định nên giữ `single_admin` và cổng mẫu. Bỏ login chỉ cho Dev localhost cô lập: `AUTH_MODE=local_demo`, `APP_ENVIRONMENT=local`, `AUTH_DEV_ADMIN_BYPASS=true`, vẫn đủ khóa và profile localhost; `SEARCH_INTERNAL_DEV_ENABLED=false`. LAN/production với bypass bị chặn.

## 2. Build và chạy toàn bộ bằng Docker

Host chỉ cần Git và Docker Desktop/Engine + Compose v2; không cần cài Python, Node, OCR/Office/embedding runtime. Model mount read-only, không nằm trong image, không tải khi startup. API, frontend và ba worker riêng; worker dùng chung Backend image; Database có Compose riêng.

### 2.1. Cấu hình ngoài Git

```bash
mkdir -p /d/etl/private /d/etl/source /d/etl/models/minilm
test -f /d/etl/private/app.env || cp deploy/.env.docker.example /d/etl/private/app.env
test -f /d/etl/private/connections.json || cp deploy/config/connections.example.json /d/etl/private/connections.json
notepad.exe D:/etl/private/app.env
```

Trong `app.env`, cập nhật các key sau; giữ nhóm còn lại theo mẫu:

```dotenv
BACKEND_IMAGE=etl-portable-backend:local
FRONTEND_IMAGE=etl-portable-frontend:local
ETL_DATA_NETWORK=etl-data
WEB_PORT=8080
MODEL_DIR=D:/etl/models/minilm
MODEL_MANIFEST_FILE=D:/etl/models/minilm/etl-model-manifest.json
SOURCE_LOCAL_DIR=D:/etl/source
CONNECTIONS_FILE=D:/etl/private/connections.json
DATABASE_MODE=same-host
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
```

`app.env` là cấu hình Compose, khác `.env` local. `SOURCE_LOCAL_DIR` là thư mục host, container đọc tại `/mnt/source`. Local auth dùng biến `*_FILE`, Docker dùng `*_SECRET_FILE`.

Sửa `D:/etl/private/connections.json`: đúng **bốn key** theo [mẫu](deploy/config/connections.example.json), thay `CHANGE_ME` bằng credentials thực. Host phải truy cập được **từ container**, không dùng `127.0.0.1`/`localhost`. SOURCE dùng reader chỉ đọc; Mongo runtime không dùng root.

- **DB Docker đã có:** dùng network/hostname service hiện có (`docker network ls`). Ví dụ DB mục 1: `ETL_DATA_NETWORK=etl-master-data_default`, host `postgres:5432`, `source-postgres:5432`, `mongo:27017`, tên DB và role theo `.env`. Không tạo DB trống khác thay DB đang dùng.
- **DB Docker mới hoàn toàn:** xem [khởi tạo Database riêng](deploy/DATABASE_SETUP.md), gồm secrets, schema và runtime roles. `DbStart` chỉ khởi động server, chưa có schema ứng dụng.
- **DB khác máy:** cấu hình DNS/TLS, `DATABASE_MODE=remote`, thêm `-RemoteDatabase`; xem [Docker chi tiết](deploy/README.md). Không mở quyền/cổng để bỏ guard.

### 2.2. Build, chuẩn bị model và auth

Thực hiện theo thứ tự: **build image → chuẩn bị model → tạo secret → điền app.env → Check/Start**. Nếu đã có model/secret hợp lệ thì bỏ qua bước tạo tương ứng. Các ví dụ dưới dùng tag `:local`; nếu đổi tag trong `app.env`, sửa cả tham số `-Image` và lệnh Docker tương ứng. `PrepareModel` mặc định dùng `etl-portable-backend:local`; nếu đổi tag, đặt `ETL_MODEL_TOOL_IMAGE` tương ứng.

**Bước1 — Build và chuẩn bị model.**

```bash
# legacy CHỈ để build trước khi có auth secret; không chạy app bỏ login.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Build -AuthProfile legacy -EnvFile D:/etl/private/app.env

# Bỏ prepare nếu đã có model/manifest ở mục local; cần Internet/license khi tải.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action PrepareModel -AuthProfile legacy -ModelDirectory D:/etl/models/minilm -AcceptModelLicense
```

**Bước2 — Tạo secret và mật khẩu admin.** Chỉ cần Docker, không cần Python trên host. Nếu đã có ba file ở mục1.4 thì dùng lại và bỏ qua lệnh này:

```bash
# Chỉ khi chưa có ba file secret; thay YOUR_EMAIL bằng email admin thực.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/setup-auth.ps1 \
  -Directory D:/etl/private/auth -AdminEmail YOUR_EMAIL -Image etl-portable-backend:local
```

Nhập/xác nhận mật khẩu ít nhất15 ký tự; không thấy ký tự khi gõ là bình thường. Email ở `-AdminEmail` là email admin bạn chọn, không cần có trong MASTER. Lệnh tạo ba file trong `D:/etl/private/auth` và in cấu hình mẫu, **không tự sửa `app.env`**.

**Bước3 — Điền cấu hình vào file Docker.** Mở đúng file sau, không sửa `.env` local để cấu hình container:

```bash
notepad.exe D:/etl/private/app.env
```

Thay các key đã có hoặc bổ sung key còn thiếu theo khối dưới; thay `YOUR_EMAIL` bằng cùng email ở bước2, giữ nguyên cấu hình DB/model đã làm ở mục2.1:

```dotenv
AUTH_MODE=single_admin
AUTH_APP_ENVIRONMENT=production
ADMIN_ENABLED=true
AUTH_DEV_ADMIN_BYPASS=false
AUTH_PUBLIC_ENABLED=false
AUTH_DEPLOYMENT_PROFILE=localhost
AUTH_COOKIE_SECURE=false
AUTH_ORIGIN=http://127.0.0.1:8080
ADMIN_LOGIN_EMAIL=YOUR_EMAIL
ADMIN_PASSWORD_HASH_SECRET_FILE=D:/etl/private/auth/admin_password_hash
SESSION_SIGNING_KEY_SECRET_FILE=D:/etl/private/auth/session_signing_key
AUTH_PROXY_KEY_SECRET_FILE=D:/etl/private/auth/auth_proxy_key
```

**Bước4 — Bật user MASTER nếu cần.** Nếu chỉ dùng admin, bỏ qua. Muốn dùng cả user MASTER và admin riêng, trong chính `D:/etl/private/app.env` đổi dòng `AUTH_MODE` hiện có và bổ sung:

```dotenv
AUTH_MODE=master_users
AUTH_MASTER_FRESHNESS_SECONDS=120
AUTH_MASTER_PBKDF2_SALT_ENCODING=auto
AUTH_MASTER_RESOURCE_GRANTS={}
```

Giữ `ADMIN_ENABLED=true` nếu cần admin riêng. Điều kiện tài khoản/quyền giống mục1.4. MASTER được đọc qua URL trong `connections.json`; không lấy `DATABASE_URL` của `.env` local để kết nối từ container. Dùng `-AuthProfile auth` cho cả `single_admin` và `master_users`; `db_users` cần cấu hình riêng theo [hướng dẫn Auth DB](deploy/AUTH_DATABASE_SETUP.md).

| Local: `.env` gốc repo | Docker: `D:/etl/private/app.env` |
|---|---|
| `AUTH_ORIGIN=http://127.0.0.1:3000` | `AUTH_ORIGIN=http://127.0.0.1:8080` |
| `ADMIN_PASSWORD_HASH_FILE` | `ADMIN_PASSWORD_HASH_SECRET_FILE` |
| `SESSION_SIGNING_KEY_FILE` | `SESSION_SIGNING_KEY_SECRET_FILE` |
| `AUTH_PROXY_KEY_FILE` | `AUTH_PROXY_KEY_SECRET_FILE` |
| `AUTH_STATE_PATH` trỏ SQLite trên host | Giữ storage session theo Compose auth, không chép đường dẫn SQLite host vào container. |

Ba biến Docker `*_SECRET_FILE` trỏ tới **file trên host** đã tạo, Compose mount chúng vào container. Có thể dùng cùng bộ secret với local; không chép nội dung hash/key vào `app.env`, image hoặc Git. `setup-auth.ps1` in ra mẫu `single_admin`; muốn dùng MASTER phải sửa mode như trên. Sau khi sửa cấu hình, chạy Check → Start ở mục2.3; giữ `-Drive` nếu stack đang dùng Drive. Không cần build lại chỉ vì đổi ENV; nếu image chưa có code xác thực mới thì Build trước.

**Bước5 — Chạy và xác nhận.** Chạy hai lệnh Check → Start ở mục2.3, sau đó mở **http://127.0.0.1:8080/login**. Admin dùng email ở bước3 và mật khẩu bước2; user MASTER dùng email/mật khẩu SOURCE đã đồng bộ. Kiểm tra mode bằng:

```bash
curl -s http://127.0.0.1:8080/api/v1/auth/config
```

Nếu đã chọn MASTER + admin, cần thấy `"mode":"master_users"`, `"login_required":true`, `"admin_feature_enabled":true`. Nếu sai, kiểm tra `-EnvFile` đúng đường dẫn và chạy Start lại với cùng profile/overlay. Nếu gặp401/403/503, xem bảng lỗi ở mục3; không tạo lại toàn bộ secret hoặc DB để chữa lỗi.

**Đổi/quên mật khẩu admin khi chỉ có Docker:** chạy từ Git Bash, dùng tag Backend khớp `BACKEND_IMAGE` trong `app.env`:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -it --user 10001:10001 \
  --mount "type=bind,source=D:/etl/private/auth,target=/output" \
  --entrypoint python etl-portable-backend:local \
  -m data_sync_etl.security.cli hash --output /output/admin_password_hash_v2
```

Nhập/xác nhận mật khẩu mới ít nhất15 ký tự. File đích phải chưa tồn tại; nếu đã có `_v2`, dùng tên mới. Sửa `ADMIN_PASSWORD_HASH_SECRET_FILE=D:/etl/private/auth/admin_password_hash_v2` trong `app.env`, rồi Start lại API. Local dùng lệnh `python -m data_sync_etl.security.cli hash --output D:/etl/private/auth/admin_password_hash_v2` và sửa `ADMIN_PASSWORD_HASH_FILE` trong `.env`. Giữ nguyên hai khóa còn lại; phiên cũ cần đăng nhập lại. Mật khẩu **user MASTER** phải đổi tại SOURCE rồi chờ đồng bộ, không dùng CLI admin này.

### 2.3. Kiểm tra, chạy và cập nhật

DB/schema/roles/network phải sẵn sàng. Network mới: tạo một lần bằng script `-Action Network -NetworkName etl-data`; bỏ nếu dùng network có sẵn.

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Check -AuthProfile auth -EnvFile D:/etl/private/app.env
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Start -AuthProfile auth -EnvFile D:/etl/private/app.env -Workers
```

Mở **http://127.0.0.1:8080/login**, Swagger **http://127.0.0.1:8080/docs** sau login. API 8000 không publish ra host. Start kiểm tra auth/model/DB, không migration; bỏ `-Workers` thì chỉ khởi động API/frontend.

**Có Drive:** cấu hình theo [Docker Drive](deploy/DRIVE_SETUP.md#5-docker), thêm `-Drive` vào Check/Start/Status/Logs/Stop; chỉ bật `.env` local không đủ. Giữ cùng overlay khi recreate.

```bash
# Đổi Status thành Logs để xem log, Stop để dừng và giữ dữ liệu.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action Status -AuthProfile auth -EnvFile D:/etl/private/app.env
# Đổi source: Build rồi Start với cùng env/profile/overlay.
```

Linux dùng script Bash; xuất/nhập `.tar`, TLS và vận hành: [deploy/README.md](deploy/README.md). Archive không kèm model, secret, database hoặc học liệu. Không dùng `down -v`/prune để chữa lỗi.

## 3. Pull lại code, đổi mật khẩu và kiểm tra

**Thông tin cá nhân:** bấm avatar trên header → **Thông tin cá nhân** (`/account/profile`). Trang giữ header, ẩn submenu, có nút quay lại và avatar mặc định. Trong mode `master_users`, họ tên/email/vai trò đọc từ MASTER; hồ sơ mở rộng lấy qua `master_users.mongo_id` → `MongoDB.users._id`. Người dùng chỉ cập nhật cơ quan, địa chỉ và giới thiệu của chính mình. Họ tên, email, vai trò, môn học và trạng thái tài khoản chỉ đọc; không ghi ngược SOURCE hoặc sửa MASTER. Nếu hồ sơ đổi ở nơi khác trong lúc chỉnh sửa, hệ thống báo xung đột và yêu cầu tải lại. Admin cấu hình riêng hoặc tài khoản chưa có liên kết Mongo chỉ xem thông tin tài khoản; không tự ghép hồ sơ theo email. Không có migration hoặc biến ENV mới cho tính năng này.

- Local: dừng API/worker, kích hoạt `.venv`, chạy lại `pip install -e ".[dev,embeddings,google-drive]"` và `npm.cmd --prefix frontend ci`, đối chiếu biến mới; không chép đè `.env`. Dùng lại model/token hợp lệ.
- Docker: giữ DB/model/secrets, **Build → Start**. `docker image load` archive cũ không cập nhật source mới. Chỉ đổi config runtime thì Start để recreate.
- Đặt lại mật khẩu **admin riêng**: dùng lệnh local/Docker ở mục2.2; mật khẩu user MASTER đổi tại SOURCE rồi đồng bộ. Không thể lấy mật khẩu gốc từ hash.
- Schema cũ cần chủ DB duyệt migration riêng. Pull/build không đồng nghĩa được phép migration DB dùng chung. Không commit `.env`, token/hash/key, dữ liệu hoặc model.

| Lỗi | Kiểm tra/sửa |
|---|---|
| `No module named argon2` | Đúng `.venv`, cài lại `python -m pip install -e ".[dev,embeddings,google-drive]"`. |
| Login trắng/auth 404 | Đúng Backend/Frontend; Docker build/recreate image mới, Ctrl+F5. |
| Login401 | Kiểm tra `AUTH_MODE`, email/mật khẩu đúng nguồn; admin riêng cần cờ bật. MASTER xem log `MASTER_LOGIN_PASSWORD_MISMATCH`, `MASTER_LOGIN_STATE_REJECTED`, `MASTER_LOGIN_IDENTITY_NOT_FOUND`. Không gửi hash/password vào chat. |
| `AUTH_MASTER_STALE` / `AUTH_MASTER_UNAVAILABLE` | Kiểm tra MASTER connection, Master Sync Worker và users sync_run COMPLETED trong freshness120s; không nới quyền hoặc bỏ kiểm tra freshness để chữa lỗi. |
| Login được nhưng403 | Kiểm tra `/api/v1/auth/me`: MASTER role code ADMIN cần ADMIN_ENABLED=true, account/role active và sync còn mới; Viewer vẫn theo ownership/public. Giữ LocalAdminBoundary. |
| `SOURCE_REFERENCE_POLICY_BLOCKED` | Bật cả hai cờ reference/temp trong **app.env**, mount nguồn đúng; Drive cần `-Drive`. |
| `DATABASE_SCHEMA_NOT_READY` | Đúng DB: 15 bảng SOURCE/18 bảng Master/5 collection Mongo. DB mới theo guide, DB cũ cần owner duyệt. |
| SOURCE role bị chặn | Reader không superuser, chỉ SELECT, không quyền ghi/kế thừa quyền ghi. |
| `CONNECTIONS_SECRET_VALUES_INVALID` | JSON đủ bốn key, không còn `CHANGE_ME`, URI/role/password đúng. |
| Index/OCR lỗi | Model/revision/384D, Tesseract `vie+eng`, LibreOffice path và worker. |
| `/health` OK, DB chưa xác định | Đăng nhập và kiểm tra trên giao diện; `/api/v1/system/health` kiểm tra Master và cần quyền admin. |

Kiểm thử source dùng fixture cô lập, không DB Dev/Production:

```bash
python -m pytest tests/unit deploy/tests -q
python -m ruff check src tests deploy
node --experimental-strip-types --test frontend/tests/*.test.mjs
npm.cmd --prefix frontend run build
git diff --check
```

Chỉ báo PASS cho phép thử đã chạy thực tế. Mặc định localhost; LAN/Production cần HTTPS, `auth-tls`, mạng và phê duyệt quyền phù hợp.

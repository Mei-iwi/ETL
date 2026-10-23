# ETL — Kho học liệu số

Đồng bộ SOURCE → MASTER, OCR/trích xuất nội dung và tìm kiếm BoW/Vector/Hybrid.
SOURCE chỉ lưu tham chiếu; tệp LOCAL dùng kho riêng. Word/PPT xem dạng PDF, XLSX/CSV xem dạng lưới; tải xuống giữ file gốc.

Lệnh dưới dùng **Git Bash trên Windows, tại gốc repo**; khối PowerShell được ghi riêng.
Mẫu cài mới dùng **BGE-M3 1024 chiều**. Giữ đường dẫn mẫu hoặc thay bằng vị trí thật trên máy; thay các `YOUR_*` bằng thông tin của bạn.

| File | Dùng cho |
| --- | --- |
| `.env` gốc repo | API/worker local; nhóm `POSTGRES_*` dùng khởi tạo DB Docker local |
| `docker.env` gốc repo | Image, model, auth, network và mode DB của ứng dụng Docker |
| `D:/etl/private/connections.json` | URL MASTER/SOURCE/Mongo cho Docker, được trỏ bởi `CONNECTIONS_FILE` |

Env/secret không được commit. Docker không lấy URL DB từ `.env` host. Nếu đã export biến trong terminal, giá trị đó có thể ghi đè env file.

## Tìm kiếm BoW/BM25 và triển khai lexical index

**Túi nghĩa tự động:** mặc định `INDEX_MEANING_BAGS_ENABLED=true`. Model multilingual
đọc các cách gọi/câu mô tả trong nội dung gốc, gom những biểu thức đủ gần nghĩa và
lưu vector cùng vị trí nguồn cho từng biểu thức. Vector/Hybrid truy vấn các nhóm
này; không còn dùng vector của 5 unigram để đại diện nghĩa khi chế độ này bật.
Xem [thiết kế, triển khai và kiểm chứng](docs/meaning-bags.md). Corpus cũ cần
validator bổ sung và full reindex; restart đơn thuần không tạo được nhóm nghĩa.
BoW dùng từ điển khái niệm được kiểm duyệt để mở rộng Việt–Anh và BM25, không gọi encoder.

`bow` dùng **BM25** trên toàn bộ nội dung ContentBag; `vector` dùng cosine của
biểu thức nguồn trong nhóm nghĩa (max cosine); `hybrid` dùng **RRF(k=60)** giữa hai thứ hạng. Nhánh Vector
mặc định chỉ nhận bag có cosine **>= `SEARCH_VECTOR_MIN_SIMILARITY` (mặc định 0.6)**;
lọc trước RRF, grouping và phân trang. Cosine dương không tự chứng minh liên quan.
Hybrid giữ kết quả BoW hợp lệ kể cả khi nhánh Vector không đạt ngưỡng; kết quả
chỉ khớp ngữ nghĩa vẫn được nhận nếu qua ngưỡng, không bắt buộc trùng từ.
Vector/Hybrid có thể trả 0 kết quả. Ngưỡng chỉ áp dụng cosine, không áp vào điểm RRF.

Với Meaning Bag, có thể bật `SEARCH_MEANING_CONTEXT_FALLBACK_ENABLED=true`
trong `.env` local hoặc `docker.env`. Khi điểm biểu thức chưa đạt ngưỡng chính,
nhánh dự phòng yêu cầu đồng thời cosine biểu thức >=
`SEARCH_MEANING_FALLBACK_MIN_SIMILARITY` (0.55) và cosine vector ContentBag
**cùng bag** >= `SEARCH_MEANING_FALLBACK_MIN_CONTEXT_SIMILARITY` (0.45).
Không gọi thêm model hoặc dịch vụ dịch; không thay đổi chỉ mục. Mặc định cờ tắt;
cần kiểm tra trên câu có/không có đáp án của kho trước khi bật ở môi trường khác.
BoW chỉ mở rộng các cặp trong từ điển: `green leaf`, `green leaves` và `lá xanh`
là cùng khái niệm. Vector/Hybrid vẫn có thể tìm ngữ nghĩa ngoài từ điển.
BoW không gọi
encoder. Năm keyword của mỗi leaf chỉ phục vụ hiển thị và biểu diễn dense cũ;
chúng không còn giới hạn query, snippet hay lexical candidates.

Analyzer dùng Underthesea 8.3.0. Script `scripts/check_lexical_analyzer.py` kiểm tra
Windows/Python 3.13 và container Linux/Python 3.12. Chuẩn hóa NFC/casefold, giữ dấu, từ thành phần,
token ghép tiếng Việt, số và C++/C#/.NET/Python3. Không tạo mọi n-gram.
Stopwords được giữ trong dữ liệu kiểm tra cụm từ/phủ định; query chỉ
có stopwords trả `QUERY_NO_KEYWORDS`, token lặp không nhân trọng số query.
BM25 không cộng điểm chỉ vì trùng stopwords trong query có nghĩa; Hybrid không
được hồi sinh một kết quả Vector dưới ngưỡng chỉ nhờ trùng `the`, `and`, `của`.
Từ phủ định `không` vẫn được giữ và cụm từ vẫn kiểm tra trên chuỗi nguồn đầy đủ.

Mỗi bag lưu `lexical`: terms/TF, length, title TF, các chuỗi surface token theo
leaf, analyzer/version và fingerprint nguồn. TF/length gồm body và title một lần;
BM25 dùng k1=1.2, b=0.75, title có TF hiệu dụng gấp 2, cụm từ nhiều từ khớp đúng
được nhân điểm 1.2. Không nối cụm từ qua hai leaf. Title ở đây là **tiêu đề bag**.
Ngữ cảnh chọn chunk theo cụm khớp nguyên văn, cụm thuật ngữ tương đương, rồi độ
phủ từ mang nội dung có trọng số theo độ hiếm trong các chunk đủ quyền. Từ nối
không tạo bằng chứng khớp; khi bằng điểm ưu tiên đoạn gọn hơn rồi thứ tự nguồn.
Snippet bỏ từ nối và ưu tiên hit ít lặp, hiển thị vùng quanh hit thay vì đầu chunk.
`context_id` luôn trỏ tới chính chunk được chọn; các đoạn liền kề chỉ bổ sung
bối cảnh. `context_evidence=lexical` cho biết đoạn có từ/cụm từ khớp, không cam kết
tương đương ngữ nghĩa toàn câu. Nếu bag chỉ khớp ở title hoặc qua Vector mà body
không có bằng chứng, `context_evidence=bag` và giao diện ghi rõ đoạn tham khảo.
Chưa có bước xếp hạng ngữ nghĩa riêng cho chunk. Không cần reindex cho thay đổi này.

Corpus statistics **N/df/avgdl** được tính lại trên tất cả bag được xác minh và
đủ quyền trong phạm vi bộ lọc, gồm cả bag không khớp query. Không có cache corpus
hay cache kết quả. Candidate retrieval hiện đọc toàn bộ corpus có giới hạn,
không lọc bằng display keywords; vượt cap báo lỗi thay vì cắt ranking. Kiểm tra
cuối request bao gồm cả nonhit vì chúng ảnh hưởng BM25. Quyền descendant,
generation, tombstone, grouping theo resource và pagination được giữ lại.
`ranking_algorithm=bm25-concept-dismax-v3`; `score_components.bow` là BM25 có trọng số và boost,
Hybrid chỉ hợp nhất thứ hạng. Response có `readiness` để báo corpus cần reindex.
Chế độ tự động dùng `ranking_algorithm=meaning-maxcosine-v1` và `rrf-meaning-v1`.
Chế độ cũ dùng `bag-cosine-threshold-v2` và `rrf-vector-threshold-v2`.
Evaluation fingerprint và marker bao gồm cấu hình nhóm nghĩa và ngưỡng cosine,
không so sánh lại run cũ như thể đang dùng cùng cấu hình.
`dense_ready_bags` xác nhận vector lưu trữ hợp lệ theo model đã cấu hình; trạng thái
model resident vẫn do endpoint model-status hiện có cung cấp. Evaluation fingerprint
bao gồm mã analyzer/BM25 để không tái sử dụng đánh giá của thuật toán khác.

Mở rộng khái niệm đọc snapshot đã kiểm duyệt và có phiên bản tại
`src/data_sync_etl/domain/data/concepts.json`, độc lập với cấu hình nhóm nghĩa
embedding. `domain/concepts.py` nhận diện cụm dài nhất, phân giải theo ngữ cảnh/
lĩnh vực, giới hạn mở rộng và giữ bằng chứng nguồn. Quan hệ rộng hơn, hẹp hơn
và liên quan mặc định tắt. Bảng hard-code `domain/multilingual.py` đã được xóa
sau khi cập nhật mọi caller sang nguồn khái niệm mới.

Analyzer mới cần lexical backfill/reindex. Chỉ thay snapshot query-time hoặc
trọng số thì không cần reindex. `scripts/reindex_lexical.py` có thể giữ dense
vector và meaning bag đã xác minh, với dry-run chỉ đọc, apply tường minh và
backup/rollback BSON. Giữ nguyên manifest migration v1; hợp đồng mới nằm tại
`migrations_mongo/lexical_v2.json`.

Chạy `python scripts/benchmark_concepts.py` để so sánh ba nhánh BM25. Fixture nhỏ
có nhãn và kết quả chạy thật nằm trong `fixtures/search/concept_*`.

**Phụ thuộc hiện tại:** build ContentBag mới vẫn cần model/schema/vector hợp lệ;
lexical readiness chưa tách thành pipeline độc lập. Backfill dưới đây giữ nguyên
vector đã xác minh, không encode, tải model hay sửa `.env`. Bag cũ hoặc analyzer
khác không được dùng như chỉ mục READY trước khi backfill/reindex.
Root chỉ có số, không có display keyword, vẫn bị trì hoãn theo điều kiện build
hiện tại; chữ số trong nội dung có keyword được đưa đầy đủ vào lexical index.

**Semantic tự động:** encode query gốc và so với các biểu thức/đoạn nội dung nguồn,
không dùng từ điển alias để quyết định khớp nghĩa. Trường keyword/vector nền vẫn
được giữ để tương thích và rollback. Khi tắt chế độ tự động, biểu diễn dense cũ
vẫn có hạn chế top5 keyword. Model không bảo đảm mọi câu/ngôn ngữ cùng nghĩa
đều được gom đúng hoặc tìm thấy. Model local
`Models/bge-m3` đã được benchmark offline và cho vector 1024 chiều, revision
`5617a9f61b028005a4858fdac845db406aefb181`. Không tự chuyển corpus/model/dimension.

**Số đo biểu diễn keyword cũ:** `python scripts/benchmark_vector_filter.py` đo model local,
biểu diễn keyword production và fixture có nhãn; ghi `storage/benchmarks/vector-filter.json`.
Trên 15 query/18 bag của fixture hiện tại, gate cũ `>0` nhận cả 270 cặp; ngưỡng
0.6 nhận 10 cặp, trong đó 9 cặp có nhãn liên quan. Đồng thời chỉ giữ 9/22 cặp
liên quan đã gán nhãn: đây là lựa chọn ưu tiên giảm nhiễu, có thể bỏ sót kết quả
đa ngôn ngữ. Fixture nhỏ và các cặp chưa có nhãn không mặc nhiên là không liên quan;
không xem số này là chất lượng trên corpus thật. Giảm ngưỡng (ví dụ 0.55) tăng độ
phủ nhưng có thể tăng nhiễu; hiệu chỉnh trên query và nhãn thực tế của corpus/model.
Chỉ cần restart API khi đổi ngưỡng, không cần reindex. Hạn chế top5 keyword ở dense
vẫn còn; ngưỡng không thay thế biểu diễn nội dung tốt hơn hoặc bộ reranker.

### Thứ tự triển khai và rollback

Các bước lexical backfill dưới đây dành cho chế độ cũ
`INDEX_MEANING_BAGS_ENABLED=false`. Với chế độ tự động mặc định, dùng validator
và full reindex trong [hướng dẫn túi nghĩa](docs/meaning-bags.md).

1. Dừng API/worker/sync đang dùng `.venv` **trước khi cài lại package**. Trên
   Windows, worker chạy bằng `etl` giữ khóa `etl.exe`, khiến pip uninstall dở dang.
   Cài dependencies bằng lệnh cài repo bên dưới. Giữ nguyên `.env`, model và số
   chiều hiện tại. Foundation v1 vẫn có 5 collection/6 business indexes; lexical
   chỉ thêm field tùy chọn ở `content_bags`. Manifest mở rộng là
   `migrations_mongo/lexical_v1.json`; không sửa migration/schema lịch sử.
2. Xác nhận target, backup hạ tầng và dừng **mọi tiến trình ghi** (API/worker/sync).
   Với database trống, chạy foundation bootstrap hiện có trước; sau đó chạy:

   ```bash
   python scripts/check_migration_governance.py
   python scripts/reindex_lexical.py --dry-run
   ```

3. Chỉ khi dry-run thành công, operator mới chạy lệnh ghi đã duyệt:

   ```bash
   python scripts/reindex_lexical.py --apply --writers-stopped --backup storage/lexical-backup.json
   python scripts/reindex_lexical.py --check
   ```

   Backup BSON chứa validator cũ, bag gốc và run IDs, đồng thời là checkpoint.
   Chạy lại cùng lệnh/cùng backup để resume. Script kiểm tra nguồn, membership,
   model/revision/dimension và TF trước khi đánh dấu run COMPLETED. Tối đa 10.000
   bag mỗi target; nguồn đã đổi hoặc vector không khớp cần full reindex bằng luồng
   indexing hiện có, không được tái sử dụng vector cũ. `--check` kiểm tra schema/
   documents; kiểm tra index-status từng version để xác nhận readiness nguồn.
   Foundation CLI `schema --check` vẫn kiểm tra contract v1 đóng băng, vì vậy sau
   extension hãy dùng `reindex_lexical.py --check` để kiểm tra schema hoạt động.
   Schema concept cũ đã biết cũng được nhận diện chính xác bằng fingerprint:
   payload `concepts` được giữ trong backup, bỏ khỏi bag khi chuyển sang lexical,
   và được khôi phục cùng validator khi rollback. Schema lạ vẫn bị từ chối.
   Bag concept lịch sử không-current cần migration được xem xét riêng.
4. Khởi động lại ứng dụng/worker, kiểm tra index-status, BoW/Hybrid và context.
   Nếu cần rollback, dừng writers, chạy:

   ```bash
   python scripts/reindex_lexical.py --rollback --writers-stopped --backup storage/lexical-backup.json
   ```

   Sau đó dùng checkout ứng dụng tương thích trước nâng cấp. Rollback từ chối
   ghi đè nếu nguồn/bag đã thay đổi; không xóa dữ liệu học liệu hay model. Backup
   phải được giữ trong kho nội bộ (mặc định `storage/` được gitignore).

### Đánh giá có thể chạy lại

```bash
python scripts/benchmark_lexical.py --output storage/benchmarks/lexical.json
python -m pytest tests/unit/test_lexical_search.py --basetemp=.test-tmp/lexical
# TEST_MONGO_URI chỉ trỏ server test; fixture tự tạo database etl_test_<UUID>.
python -m pytest tests/integration/test_lexical_mongo.py --basetemp=.test-tmp/lexical-mongo
```

Corpus/qrels: `fixtures/search/lexical_benchmark.json`; kết quả model thật:
`fixtures/search/lexical_benchmark_results.json`. Cùng 18 bag/15 query, k=5,
3 lần/query; latency là trung bình median mỗi query, chỉ đo ranking/encode offline,
không bao gồm DB, quyền, verification, HTTP hoặc cache. Bộ dữ liệu nhỏ được gán
nhãn thủ công để phát triển, chưa chứng minh chất lượng trên học liệu thực tế.

| Phương pháp | Recall@5 | nDCG@5 | MRR | Latency ms |
| --- | ---: | ---: | ---: | ---: |
| Binary top5 cũ | 0.511 | 0.481 | 0.580 | 0.029 |
| BM25 | 0.778 | 0.823 | 0.933 | 0.223 |
| Hybrid + BGE-M3 thật | 0.878 | 0.786 | 0.843 | 121.304 |

BM25 không giảm nDCG so baseline trên bộ này. Hybrid giảm so BM25 ở `phrase`,
`vi-en`, `vi-natural`; tăng recall không đồng nghĩa cải thiện thứ tự mọi query.
Các truy vấn exact ngoài top5, Việt/Anh, mô tả tự nhiên, đa nghĩa và khái niệm
liên quan nhưng không tương đương đều có trong fixture. Vector benchmark giữ
đúng keyword input của production hiện tại, không dùng fake vectors để suy luận
chất lượng semantic.

Kiểm chứng triển khai ngày 22/10/2026: 245 test liên quan search/indexing/quyền/
context/cache và 43 Mongo integration pass (1 skip), 10 test lexical riêng,
37 test evaluation và 45 frontend API/runtime test pass; Python lint, frontend lint/build, diff check
và migration governance pass. Suite unit rộng ban đầu có 1.240 pass/9 skip/4 lỗi;
hai hồi quy schema-readiness và `word` → `word42` đã được sửa và kiểm tra lại.
Hai lỗi còn lại (snapshot OpenAPI, capabilities modes) và test UI xem Office
đều tái hiện trên HEAD gốc trích riêng dưới `.test-tmp/lexical-baseline`, không
được sửa snapshot để che lỗi. PostgreSQL integration/concurrency chưa được chạy
do chưa có TEST_DATABASE_URL cô lập; Mongo tests dùng UUID riêng, metadata fixture
dùng SQLite. Không apply lên database đang phục vụ.

## 1. Cài môi trường và dependencies

## 1. Chạy local

### 1.1. Cài môi trường và thư viện

Cần Git, Python3.12, Node.js22.12+ và Docker Desktop (Linux containers) nếu dùng DB Docker.

```bash
py -3.12 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,embeddings,google-drive]"
npm.cmd --prefix frontend ci
test -f .env || cp .env.example .env
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/source-temp /d/etl/storage /d/etl/models/bge-m3
```

Linux: dùng `python3.12`, `source .venv/bin/activate`, `npm` thay `npm.cmd`, và đường dẫn tuyệt đối Linux thay `D:/etl/...`.
Không dùng `~` trong env; không chép `.venv` từ thư mục/máy khác.

Nếu cài lại gặp `WinError 32` tại `.venv/Scripts/etl.exe`, dừng terminal worker
bằng Ctrl+C trước khi chạy lại pip; không tiếp tục apply sau khi pip báo lỗi.
Uninstall dở dang có thể làm mất editable import và gây `ModuleNotFoundError`.
Sau khi dừng các tiến trình, có thể sửa bản cài bằng dependencies đã có:

```bash
python -m pip install -e . --no-deps --no-build-isolation
python -c "import data_sync_etl; print(data_sync_etl.__file__)"
python scripts/reindex_lexical.py --dry-run
```

## 2. Cấu hình `.env`

### 1.2. Cài OCR và công cụ xem Office

**PowerShell Windows:**

```powershell
winget install --id UB-Mannheim.TesseractOCR --exact
winget install --id TheDocumentFoundation.LibreOffice --exact
& 'C:\Program Files\Tesseract-OCR\tesseract.exe' --list-langs
& 'C:\Program Files\LibreOffice\program\soffice.exe' --version
```

Cần cả `eng` và `vie`. Thiếu ngôn ngữ thì chạy **PowerShell bằng Administrator**:

```powershell
$tessdata = 'C:\Program Files\Tesseract-OCR\tessdata'
foreach ($language in @('eng', 'vie')) {
    $target = Join-Path $tessdata ($language + '.traineddata')
    if (-not (Test-Path -LiteralPath $target)) {
        Invoke-WebRequest "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/$language.traineddata" -OutFile $target
    }
}
```

**Ubuntu/Debian:**

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-vie libreoffice-writer libreoffice-impress
tesseract --list-langs
```

Tham khảo [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html) và [LibreOffice](https://www.libreoffice.org/installation-instructions/).

### 1.3. Chuẩn bị BGE-M3

Có model/manifest hợp lệ rồi thì bỏ bước tải; giữ đường dẫn đang có. Nếu chưa có, chạy trong venv:

```bash
python deploy/runtime/model.py prepare \
  --directory D:/etl/models/bge-m3 --manifest D:/etl/models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 \
  --dimension 1024 --accept-model-license
```

Tải cần Internet/license; sau đó kiểm tra nạp offline:

```bash
python deploy/runtime/model.py encode \
  --directory D:/etl/models/bge-m3 --manifest D:/etl/models/bge-m3/etl-model-manifest.json \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
```

Cần `status=PASS`, `dimension=1024`. Manifest chứa revision/checksum; ứng dụng không tự tải model khi khởi động.
Công cụ model không đọc `.env`, nên truyền đủ tham số như trên.

### 1.4. Cấu hình `.env`

Mở `notepad.exe .env`, **sửa key đã có, không thêm trùng**. Ví dụ DB local:

```dotenv
APP_ENVIRONMENT=development
ADMIN_ENABLED=true
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
MASTER_SYNC_AUTO_INTERVAL_SECONDS=30
INDEX_AUTO_ENABLED=false
SEARCH_INTERNAL_DEV_ENABLED=false
```

Linux: executable `/usr/bin/tesseract`, `/usr/bin/libreoffice` và đường dẫn host thực.
Password chứa ký tự đặc biệt phải percent-encode trong URL; `POSTGRES_PASSWORD` giữ giá trị gốc.
SOURCE dùng reader chỉ SELECT. Bỏ/comment `SOURCE_MIGRATION_DATABASE_URL` khi vận hành.
Kho nguồn đọc `<root>/<storage_bucket>/<storage_object_key>` theo metadata; kho SOURCE/LOCAL/thư mục tạm phải tách biệt.

### 1.5. Chuẩn bị DB

**DB có sẵn:** bỏ khởi tạo/migration. Nếu container local đang dừng:

```bash
docker compose up -d postgres source-postgres mongo
```

**Chỉ DB local mới/trống của riêng bạn:** sau khi đặt dimension1024 trong `.env`:

```bash
docker compose up --build -d
docker compose ps -a
docker compose logs master-migrate source-migrate mongo-init
```

Ba job phải exit0. Không chạy bước này trên DB remote/Dev dùng chung/Production. DB mới chưa có học liệu/users nghiệp vụ.
Tạo reader cho SOURCE local mới:

```bash
docker compose exec -T source-postgres psql -v ON_ERROR_STOP=1 -U etl_source_admin -d etl_source < scripts/sql/source_reader.sql
docker compose exec source-postgres psql -U etl_source_admin -d etl_source
```

Trong psql: `\password etl_source_reader`, nhập password khớp `.env`, rồi `\q`.
Nếu đổi tên DB/user, sửa lệnh và `GRANT CONNECT` trong SQL. Không xóa reader đã tồn tại để cấp lại quyền.

```bash
python -m data_sync_etl.adapters.mongo.schema --check
```

Cần `schema_ready=true`. DB thuần1024 dùng history `[]`; có dữ liệu384 thì xem mục4.

### 1.6. Đăng nhập

Tạo secret một lần, bỏ qua nếu ba file đã tồn tại:

```bash
python -m data_sync_etl.security.cli init --directory D:/etl/private/auth
```

Nhập password admin ít nhất15ký tự; khi gõ không hiện ký tự. Trong `.env`:

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

Dùng cả user MASTER/admin: đổi `AUTH_MODE=master_users`, thêm `AUTH_MASTER_FRESHNESS_SECONDS=120`, `AUTH_MASTER_PBKDF2_SALT_ENCODING=auto`.
User đăng nhập bằng password tài khoản SOURCE đã đồng bộ, không phải password DB. Account/role phải active, email verified và users sync còn mới.
`ADMIN_ENABLED=true` bật tính năng, không bỏ login. Không có password mặc định.
[Chi tiết MASTER users](deploy/AUTH_MASTER_USERS_SETUP.md); Auth DB độc lập dùng [db_users](deploy/AUTH_DATABASE_SETUP.md).

### 1.7. Khởi động local

Mở5terminal; mỗi terminal Python kích hoạt venv:

| Tiến trình | Lệnh |
| --- | --- |
| API | `python -m uvicorn data_sync_etl.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers` |
| React | `npm.cmd --prefix frontend run dev` |
| Master Sync | `python -m data_sync_etl.cli run-master-sync-worker --worker-id master-sync-1` |
| Source File | `python -m data_sync_etl.cli run-source-file-worker --worker-id source-file-1` |
| OCR/Index | `python -m data_sync_etl.cli run-ocr-worker --worker-id ocr-1` |

Đăng nhập **http://127.0.0.1:3000/login**; Swagger admin qua **http://127.0.0.1:3000/docs**.
Trong trang Đồng bộ dữ liệu nguồn, chạy Full baseline rồi bật Auto Incremental, nhất là nhóm users khi dùng `master_users`.
Chạy worker chưa tự bật lịch. Khoảng30s chỉ là mặc định cho cấu hình đồng bộ mới; kiểm tra lịch đã lưu nếu DB có sẵn.
Sửa `.env` phải restart API/cả3worker; đổi proxy key/origin thì restart Vite. Dừng bằng Ctrl+C.

## 2. Chạy bằng Docker

Host chỉ cần Git và Docker Desktop/Engine + Compose v2. Image chứa Python3.12, Tesseract `vie+eng`, LibreOffice và embedding dependencies.
Model/manifest và secret đặt riêng trên host, không nằm trong image.

### 2.1. Tạo cấu hình

```bash
mkdir -p /d/etl/private/auth /d/etl/source /d/etl/models/bge-m3
test -f docker.env || cp deploy/docker.env.example docker.env
test -f /d/etl/private/connections.json || cp deploy/config/connections.example.json /d/etl/private/connections.json
notepad.exe docker.env
notepad.exe D:/etl/private/connections.json
```

File mẫu Docker đã dùng BGE-M3 1024. Kiểm tra các giá trị này trước khi chạy:

| Key trong docker.env | Giá trị mẫu/cách điền |
| --- | --- |
| `BACKEND_IMAGE`, `FRONTEND_IMAGE` | `etl-portable-backend:local`, `etl-portable-frontend:local`; giữ cùng tag khi Build/Export |
| `MODEL_DIR`, `MODEL_MANIFEST_FILE` | `D:/etl/models/bge-m3`, `D:/etl/models/bge-m3/etl-model-manifest.json`; có model ở nơi khác thì sửa cả hai |
| `CONNECTIONS_FILE` | `D:/etl/private/connections.json` |
| `SOURCE_LOCAL_DIR` | `D:/etl/source` hoặc thư mục nguồn thực tế |
| `ADMIN_LOGIN_EMAIL` | Email admin bạn chọn, không để `SET_YOUR_ADMIN_EMAIL` |
| Ba key `*_SECRET_FILE` | File trong `D:/etl/private/auth`, được tạo ở bước2.3 |
| `DATABASE_MODE` | `same-host`; chỉ SOURCE Neon dùng `source-neon`; các DB remote TLS dùng `remote` |

Nếu dùng DB remote, điền URL/CA theo mục3 trước khi VerifyModel/Check/Start; chưa cần khởi động pipeline.
Được phép đọc nguồn thì đổi hai key hiện có:

```dotenv
SOURCE_REFERENCE_ENABLED=true
SOURCE_TEMPORARY_FILES_ENABLED=true
```

Ví dụ JSON cho stack DB local ở mục1.5:

```json
{
  "DATABASE_URL": "postgresql+psycopg://etl:YOUR_MASTER_PASSWORD@postgres:5432/etl",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:YOUR_READER_PASSWORD@source-postgres:5432/etl_source",
  "MONGO_URI": "mongodb://mongo:27017",
  "MONGO_DATABASE": "etl_content"
}
```

JSON chỉ chứa đúng4key, password phải thật. Mongo đã bật auth thì dùng account/`authSource` được cấp, không tắt auth để khớp ví dụ local.
Không dùng `127.0.0.1` cho DB container: bên trong app địa chỉ đó là chính app, không phải host.

### 2.2. Chuẩn bị DB và network

DB chưa có thì dùng mục1.4–1.5 để tạo DB local bằng Docker; không cần Python host cho bước khởi tạo đó.
DB đã có thì không chạy migration. Ứng dụng Docker không tự tạo schema.
Tạo network ứng dụng, nối DB có sẵn:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Network
docker network connect --alias postgres etl-data etl-master-data-postgres-1
docker network connect --alias source-postgres etl-data etl-master-data-source-postgres-1
docker network connect --alias mongo etl-data etl-master-data-mongo-1
```

Tên container lấy từ `docker ps -a`; ví dụ trên là stack Compose gốc. Endpoint already exists nghĩa là đã nối, bỏ qua lệnh đó.
SOURCE là Neon thì bỏ lệnh nối `source-postgres`; MASTER/Mongo local vẫn phải nối.
Xem các container đã nối bằng `docker network inspect etl-data`.

Có thể dùng [Compose DB riêng](deploy/compose.database.yml) thay stack gốc, nhưng cần DBA provision schema/roles/validators trước;
MASTER service khi đó là `master-postgres`, sửa JSON tương ứng. Không nối hai DB khác nhau cùng alias vào một network.

### 2.3. Build → model → secret

**Build trước**, không Export hoặc PrepareModel khi image chưa tồn tại:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Build
```

Có model/manifest rồi bỏ lệnh tải. Nếu chưa có:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 \
  -Action PrepareModel -ModelDirectory D:/etl/models/bge-m3 \
  -ModelId BAAI/bge-m3 -ModelRevision 5617a9f61b028005a4858fdac845db406aefb181 \
  -ModelDimension 1024 -AcceptModelLicense
```

Chuẩn bị mặc định dùng image Backend `:local`; đổi tag thì export `ETL_MODEL_TOOL_IMAGE` cho đúng.
Kiểm tra model offline; `legacy` ở lệnh này chỉ chạy công cụ model, không mở API/bỏ đăng nhập ứng dụng:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action VerifyModel -AuthProfile legacy
```

Tạo secret nếu chưa có, thay email bằng email thật:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/setup-auth.ps1 \
  -Directory D:/etl/private/auth -AdminEmail YOUR_ADMIN_EMAIL -Image etl-portable-backend:local
```

Nhập password ít nhất15ký tự. Script không tự sửa `docker.env`; kiểm tra email/ba secret path.
Đã có secret từ local thì dùng lại, không init lại. Dùng MASTER users: `AUTH_MODE=master_users`,
`AUTH_MASTER_FRESHNESS_SECONDS=120`, `AUTH_MASTER_PBKDF2_SALT_ENCODING=auto` trong `docker.env`.

### 2.4. Start, Stop và cập nhật

DB/network/model/secret phải sẵn sàng. Start tự chạy preflight; Check là lựa chọn kiểm tra trước:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Start -Workers
```

Mở **http://127.0.0.1:8080/login**; Swagger admin **http://127.0.0.1:8080/docs**.
`-Workers` khởi động cả Master Sync, Source File và OCR/Index. Bỏ cờ thì Start chỉ tạo mới API/Frontend.
Check đọc DB thật đã cấu hình; Start/worker có thể ghi MASTER/Mongo và đọc nguồn, chỉ chạy trên môi trường được phép.

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Logs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Stop
```

| Vừa đổi | Chạy lại |
| --- | --- |
| Source code | Build → Start -Workers |
| docker.env | Start -Workers để Compose tạo lại container cần thiết |
| Nội dung JSON/secret, path giữ nguyên | Stop → Check → Start -Workers để tiến trình đọc lại |
| Model/dimension | VerifyModel và kiểm tra schema/index trước Start |

Launcher ưu tiên `-EnvFile` → `ETL_DOCKER_ENV_FILE` → `docker.env` gốc → `deploy/.env.docker` cũ.
Kiểm tra đường dẫn env/profile được in ra. Vẫn truyền file `app.env` cũ thì phải sửa file đó hoặc bỏ tham số.
Không dùng `down -v` để sửa lỗi; Stop ứng dụng không dừng DB.

### 2.5. Linux và chuyển máy

Linux thay các path host trong `docker.env` bằng đường dẫn tuyệt đối của máy. Các lệnh tương đương:

```bash
bash deploy/scripts/etl-docker.sh build
bash deploy/scripts/etl-docker.sh network
# Có model/secret/DB rồi:
bash deploy/scripts/etl-docker.sh check
bash deploy/scripts/etl-docker.sh start --workers
bash deploy/scripts/etl-docker.sh stop
```

Nếu chưa có model/secret trên Linux:

```bash
ETL_MODEL_TOOL_IMAGE=etl-portable-backend:local bash deploy/scripts/etl-docker.sh prepare-model \
  "$HOME/etl/models/bge-m3" --accept-model-license \
  --model-id BAAI/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --dimension 1024
sudo install -d -o 10001 -g 10001 -m 0700 /srv/etl-private/auth
sudo env ETL_AUTH_SETUP_IMAGE=etl-portable-backend:local bash deploy/scripts/setup-auth.sh /srv/etl-private/auth YOUR_ADMIN_EMAIL
```

Sửa đường dẫn model/secret trong env theo vị trí vừa tạo. File private cần container UID10001 đọc được;
proxy key cần group10001 đọc được, không chmod777. LAN cần auth/TLS profile riêng, không bật bypass localhost.

**Máy xuất**, sau khi Build cả hai image đúng tag:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Export -Archive D:/etl/etl-images.tar
```

Chỉ khi thành công mới có cặp `.tar`/`.tar.sha256`. Chép đủ hai file sang **máy nhận**, rồi chạy tại máy nhận:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/scripts/etl-docker.ps1 -Action Import -Archive D:/etl/etl-images.tar
```

Không Import ngay trên máy xuất. Chuyển/cấp riêng model, nguồn file, DB và secret; tar image không chứa dữ liệu volume.

## 3. Đổi DB local sang remote / Neon

Đổi URL chỉ đổi đích kết nối, không chuyển schema/dữ liệu hoặc reset cursor. DB remote phải có schema/roles sẵn;
kiểm tra ID/checkpoint trước khi tiếp tục đồng bộ. SOURCE dùng reader, không owner; không tự chạy migration remote.

| Cần đổi | Local `.env` | Docker |
| --- | --- | --- |
| MASTER | `DATABASE_URL` | Cùng key trong JSON |
| SOURCE | `SOURCE_DATABASE_URL` | Cùng key trong JSON |
| Mongo | `MONGO_URI`, `MONGO_DATABASE` | Cùng key trong JSON |
| CA/TLS | URL trỏ CA trên host | Env trỏ CA host; URL trỏ CA tại `/run/secrets/...` |

### 3.1. Chỉ SOURCE chuyển Neon

**Local**, giữ MASTER/Mongo hiện có, thay:

```dotenv
SOURCE_ADAPTER=postgresql
SOURCE_DATABASE_URL=postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@ep-YOUR-ENDPOINT.REGION.aws.neon.tech:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=D:/etl/private/neon-ca.pem
```

**Docker**, trong `docker.env`:

```dotenv
DATABASE_MODE=source-neon
SOURCE_DATABASE_CA_FILE=D:/etl/private/neon-ca.pem
```

Trong JSON, giữ ba key MASTER/Mongo, thay riêng giá trị `SOURCE_DATABASE_URL` bằng:

```text
postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@ep-YOUR-ENDPOINT.REGION.aws.neon.tech:5432/Learning_Resources?sslmode=verify-full&channel_binding=require&sslrootcert=/run/secrets/source-database-ca.pem
```

Lấy host/database thực từ Neon → Connect; worker có thể dùng host trực tiếp không `-pooler`.
`Learning_Resources` là database, không phải schema. DBA cấp reader trên đúng DB; nếu dùng SQL mẫu,
đổi `GRANT CONNECT ON DATABASE etl_source` thành tên DB thật, ví dụ `"Learning_Resources"`.

CA cần tồn tại trước VerifyModel/Check/Start. Có Backend image rồi có thể lấy trust bundle công khai (Git Bash):

```bash
test -f /d/etl/private/neon-ca.pem || MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only \
  --entrypoint cat etl-portable-backend:local /etc/ssl/certs/ca-certificates.crt > /d/etl/private/neon-ca.pem
```

Local chưa có image có thể dùng bundle do DBA cấp hoặc certifi:
`python -c "import certifi,shutil; shutil.copyfile(certifi.where(), 'D:/etl/private/neon-ca.pem')"` — chỉ tạo nếu file chưa có.
Launcher tự thêm overlay Neon; MASTER phải dùng alias `postgres`/`master-postgres`, Mongo dùng `mongo` trên `etl-data`.
`SOURCE_POSTGRES_*` còn trong `.env` chỉ dành cho DB Docker cũ, không chọn nguồn ETL. Không xóa volume cũ.

### 3.2. DB remote TLS khác

**Docker**, đặt trong `docker.env`:

```dotenv
DATABASE_MODE=remote
DATABASE_CA_FILE=D:/etl/private/database-ca.pem
```

JSON mẫu, dùng trust bundle hợp lệ cho cả PostgreSQL/Mongo:

```json
{
  "DATABASE_URL": "postgresql+psycopg://MASTER_USER:ENCODED_PASSWORD@master-db.example.com:5432/etl?sslmode=verify-full&sslrootcert=/run/secrets/database-ca.pem",
  "SOURCE_DATABASE_URL": "postgresql+psycopg://etl_source_reader:ENCODED_PASSWORD@source-db.example.com:5432/etl_source?sslmode=verify-full&sslrootcert=/run/secrets/database-ca.pem",
  "MONGO_URI": "mongodb://MONGO_USER:ENCODED_PASSWORD@mongo-db.example.com:27017/etl_content?authSource=etl_content&tls=true&tlsCAFile=/run/secrets/database-ca.pem",
  "MONGO_DATABASE": "etl_content"
}
```

**Local** dùng các URL tương tự trong `.env`, nhưng thay `/run/secrets/database-ca.pem` bằng `D:/etl/private/database-ca.pem`.
Mongo Atlas dùng URI/SRV/CA theo nhà cung cấp. Neon trong mode remote thêm `channel_binding=require`.
Mode remote yêu cầu các kết nối có TLS; chỉ SOURCE Neon + DB local không TLS thì dùng `source-neon`.
Giữ hostname khớp chứng chỉ và firewall cho container truy cập DB. Không tắt TLS để chữa lỗi.
Sau đổi JSON/CA: **Stop → Check → Start -Workers**. Network `etl-data` vẫn cần tồn tại kể cả DB remote.

## 4. Vận hành và xử lý lỗi

- **SOURCE file:** metadata DB không tự cấp quyền đọc file. File phải đúng bucket/object_key, trạng thái nguồn và hash; Docker mount nguồn read-only.
- **Google Drive:** [Drive setup](deploy/DRIVE_SETUP.md). Local dùng `SOURCE_DRIVE_CREDENTIALS_FILE`; Docker dùng `DRIVE_CREDENTIALS_FILE`, mapping bucket và `-Drive` cho các lệnh launcher; Bash dùng `ETL_WITH_DRIVE=true`.
- **Auto index:** chỉ bật `INDEX_AUTO_ENABLED=true` sau khi model/schema hợp lệ, restart API/OCR Worker. Nội dung cần CONTENT_READY; chỉ mục cũ/thất bại dùng Tái lập.
- **Vector384 cũ:** không dùng lại như vector1024. Backup/maintenance riêng bằng `scripts/transition_embedding_model.py`; `[384]` chỉ dùng khi validator đã chuyển để giữ lịch sử. DB thuần1024 giữ `[]`.
- **Quên password admin:** `python -m data_sync_etl.security.cli hash --output D:/etl/private/auth/admin_password_hash_v2`, sửa đường dẫn secret rồi restart; file đích phải mới. Password user MASTER đổi tại SOURCE.

| Lỗi | Kiểm tra |
| --- | --- |
| Thiếu `argon2` hoặc launcher trỏ thư mục cũ | Cài dependencies trong đúng venv; tạo lại venv nếu đã di chuyển repo |
| `AUTH_MASTER_STALE` | Master Sync Worker, Full baseline và Auto users; users COMPLETED trong120s, không bỏ freshness guard |
| `CONTAINER_OPERATION_BLOCKED` | Xác định bước auth/DB; kiểm tra DB đang chạy, alias `postgres`/`mongo` trên `etl-data`, URL và quyền; không in secret ra log |
| `DATABASE_SCHEMA_NOT_READY` | Đúng database/schema đã provision; không tự migration remote |
| `SOURCE_READ_ONLY_ROLE_REQUIRED` / `SOURCE_WRITE_PRIVILEGES_FORBIDDEN` | DBA kiểm tra reader; không dùng owner thay thế |
| `BLOCKED_INDEX_SCHEMA` / `MONGO_SCHEMA_NOT_READY` | Dimension/history khớp validator; chạy schema --check |
| `SOURCE_REFERENCE_DISABLED` / `SOURCE_REFERENCE_POLICY_BLOCKED` | Hai cờ reference/temp và quyền nguồn |
| Export báo No such image | Build cả hai image đúng tag trước; không Import khi Export thất bại |
| Mount `/absolute/private/...` không tồn tại | Sửa placeholder trong env sang file secret thực, không tạo lại secret đã có |
| `/openapi.json`403 | Đăng nhập admin, mở docs qua frontend3000/8080, không trực tiếp8000 trong auth mode |
| Search rỗng | CONTENT_READY, index READY, đúng chủ sở hữu/đủ điều kiện public |

Kiểm thử fixture cô lập, không dùng DB Dev/Production:

```bash
python -m pip install PyYAML
python -m pytest deploy/tests tests/unit/test_schema_diagnostics.py tests/unit/test_resource_description.py -q
npm.cmd --prefix frontend run build
node --experimental-strip-types --test frontend/tests/fileReadiness.test.mjs frontend/tests/sourceReference.test.mjs
```

Nếu `frontend/dist` bị khóa: `npm.cmd --prefix frontend run build -- --outDir ../.test-tmp/frontend-verify`.
Tài liệu chi tiết: [Docker](deploy/README.md), [MASTER auth](deploy/AUTH_MASTER_USERS_SETUP.md), [Auth DB](deploy/AUTH_DATABASE_SETUP.md), [BGE-M3 Chunk Search opt-in](research/bge-m3/README.md), [đánh giá chất lượng Vector](research/semantic-quality/README.md), [tối ưu và no-answer](research/semantic-quality/optimization/README.md).

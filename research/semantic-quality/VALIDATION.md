# Lệnh kiểm tra và tái lập

Thư mục làm việc: root repo, PowerShell. Các đường dẫn `.test-tmp` là evidence private, bị Git bỏ qua.
Không cần đổi `.env`, bật semantic hoặc chạy migration/reindex để đọc báo cáo.

## Audit local READ ONLY đã thực hiện

```powershell
./.venv/Scripts/python.exe scripts/export_semantic_quality.py --output .test-tmp/semantic-quality/verified
```

PASS:32 resources,363 Chunk; checksum trong `audit.json`. Chỉ MASTER/Mongo loopback,
không SOURCE. Script từ chối remote và output ngoài `.test-tmp`.

## Model thật, offline

Venv cô lập đã provision trước nhiệm vụ: Python3.13, FlagEmbedding1.3.5, ST3.4.1,
Transformers4.51.3, PEFT0.15.2; model local có revision marker và dense/ColBERT/sparse heads.
Không nâng cấp package của API đang chạy.

Lần đầu tạo NPZ và đo encoding; các vòng sau cùng corpus/model/text tái sử dụng NPZ,
kiểm tra audit và chốt16 validation/14 test. Lệnh có redirect PowerShell đã chạy:

```powershell
./.test-tmp/bge-semantic-venv/Scripts/python.exe scripts/evaluate_semantic_quality.py --corpus .test-tmp/semantic-quality/verified/corpus.json --spec research/semantic-quality/query-specification.json --model-path Models/bge-m3 --revision 5617a9f61b028005a4858fdac845db406aefb181 --output .test-tmp/semantic-quality/experiment 2> .test-tmp/semantic-quality/experiment-stderr.log
```

Artifact hoàn tất:120 rows,12 ColBERT comparisons, status PARTIAL do nhãn DRAFT.
PowerShell5 chuyển warning tokenizer trên stderr thành NativeCommandError/exit1;
không báo lệnh shell này PASS. Raw progress log còn giữ ở private output.
Đã chạy lại đầy đủ bằng wrapper dưới: **Python exit0**,120 rows/12 ColBERT comparisons;
metrics chất lượng không đổi, status vẫn **PARTIAL** vì nhãn DRAFT. Wrapper nhận đúng returncode
của process Python, không bị PowerShell diễn giải stderr:

```powershell
./.venv/Scripts/python.exe -c "import subprocess; from pathlib import Path; p=Path('.test-tmp/semantic-quality'); p.mkdir(parents=True,exist_ok=True); args=['.test-tmp/bge-semantic-venv/Scripts/python.exe','scripts/evaluate_semantic_quality.py','--corpus',str(p/'verified/corpus.json'),'--spec','research/semantic-quality/query-specification.json','--model-path','Models/bge-m3','--revision','5617a9f61b028005a4858fdac845db406aefb181','--output',str(p/'experiment')]; log=(p/'experiment-native-stderr.log').open('w',encoding='utf-8'); result=subprocess.run(args,stderr=log); log.close(); raise SystemExit(result.returncode)"
```

Không chạy trên corpus không được phép export.
Full report/NPZ private: `.test-tmp/semantic-quality/experiment/`; first encoding measurements:
`.test-tmp/semantic-quality/experiment-initial.json`. `results.json` chỉ đưa vào repo metrics/IDs/query,
không chép nội dung corpus, vector hoặc secret. Nhãn cuối không được chỉnh theo thứ hạng model;
C2 bổ sung dẫn chứng thân bài và split D1/D2 được cố định trước khi xem ranking.

Parity30 query (model local, cùng checkpoint, private helper):

```powershell
./.test-tmp/bge-semantic-venv/Scripts/python.exe -X utf8 .test-tmp/semantic-quality/check_query_parity.py
```

PASS; kết quả versionable trong `query-encoder-parity.json`. Helper này chỉ phục vụ audit tại workspace,
không phải lệnh runtime/distribution. Không khẳng định package của process API giống venv này.

## Backend trước/sau sửa

Baseline **204 PASS**:

```powershell
./.venv/Scripts/python.exe -m pytest tests/unit/test_semantic.py tests/unit/test_search.py tests/unit/test_indexing.py tests/unit/test_search_access.py tests/unit/test_owner_search_scope.py tests/unit/test_http_search_query_cache.py tests/unit/test_evaluation.py tests/unit/test_evaluation_metrics.py tests/unit/test_search_model_lifecycle.py -q --disable-warnings -p no:cacheprovider --junitxml=.test-tmp/semantic-quality/baseline-tests.xml
```

Hai lỗi trước sửa **2 FAIL có chủ đích**, evidence `correctness-before.xml`:

```powershell
./.venv/Scripts/python.exe -m pytest tests/unit/test_semantic.py -k 'diversifies_versions or inaccessible_version' -q --disable-warnings -p no:cacheprovider --junitxml=.test-tmp/semantic-quality/correctness-before.xml
```

Sau sửa:5 PASS ở nhánh inaccessible/rerank;40 PASS suite semantic+quality;216 PASS trong Docker bên dưới.
Auth **87 PASS**, evidence `auth-regression.xml`:

```powershell
./.venv/Scripts/python.exe -m pytest tests/unit/test_single_admin_auth.py tests/unit/test_master_auth.py tests/unit/test_database_auth.py -q --disable-warnings -p no:cacheprovider --junitxml=.test-tmp/semantic-quality/auth-regression.xml
```

## Docker QA thực tế

Image QA Python3.12 đã có từ nhiệm vụ trước: `etl-portable-backend:bge-chunk-qa-tests`.
Mount source/tests hiện tại read-only để kiểm tra thay đổi lần này, không tuyên bố image app cũ đã chứa patch mới.
Không build/redeploy live hoặc mount secret. Kết quả **216 PASS,2 warnings**,145.77s:

```powershell
$repo = (Get-Location).Path
docker run --rm --network none --read-only `
  --tmpfs /tmp:rw,exec,uid=10001,gid=10001,size=536870912 `
  --tmpfs /run/etl:rw,noexec,uid=10001,gid=10001,size=268435456 `
  --memory 3g --cpus 2 -e TMPDIR=/tmp `
  --mount "type=bind,source=$repo/src,target=/app/src,readonly" `
  --mount "type=bind,source=$repo/tests,target=/app/tests,readonly" `
  --mount "type=bind,source=$repo/.test-tmp/semantic-quality,target=/evidence" `
  --entrypoint python etl-portable-backend:bge-chunk-qa-tests -m pytest `
  /app/tests/unit/test_vector_quality.py /app/tests/unit/test_semantic.py `
  /app/tests/unit/test_search.py /app/tests/unit/test_indexing.py `
  /app/tests/unit/test_search_access.py /app/tests/unit/test_owner_search_scope.py `
  /app/tests/unit/test_http_search_query_cache.py /app/tests/unit/test_evaluation.py `
  /app/tests/unit/test_evaluation_metrics.py /app/tests/unit/test_search_model_lifecycle.py `
  -q --disable-warnings -p no:cacheprovider --junitxml=/evidence/docker-regression.xml
```

## Frontend và style checks

```powershell
node --experimental-strip-types --test frontend/tests/searchApi.test.mjs frontend/tests/searchRuntime.test.mjs frontend/tests/searchModelStatus.test.mjs
npm.cmd --prefix frontend run build -- --outDir ../.test-tmp/semantic-quality/frontend-build
```

Node **46 PASS**; TypeScript/Vite **PASS** (Vite8.3.3). `npm` trỏ `npm.ps1` bị ExecutionPolicy chặn,
dùng `npm.cmd` đã chạy thành công, không thay ExecutionPolicy.

Edge E2E dùng build vừa tạo, fixture HTTP server của test (không live DB):

```powershell
New-Item -ItemType Directory -Path .test-tmp/semantic-quality/ui-current -Force | Out-Null
Copy-Item -LiteralPath .test-tmp/semantic-quality/frontend-build -Destination .test-tmp/semantic-quality/ui-current/dist -Recurse
$env:ETL_T4_FRONTEND_ROOT = Join-Path (Get-Location) '.test-tmp/semantic-quality/ui-current'
node --test frontend/tests/searchUi.test.mjs
```

Copy chỉ dành cho folder evidence mới; nếu tái dùng folder đã có `dist`, đặt một folder mới để tránh lồng thư mục.
Lần chạy trong sandbox bị timeout `Page.enable` trước khi mở trang; chạy lại ngoài sandbox
với cùng build mới **PASS1**,6.90s. Không nới Auth hoặc sửa UI để xử lý timeout CDP.

```powershell
./.venv/Scripts/python.exe -m ruff check --no-cache scripts/evaluate_semantic_quality.py scripts/export_semantic_quality.py src/data_sync_etl/application/vector_quality.py src/data_sync_etl/application/semantic.py src/data_sync_etl/domain/semantic.py tests/unit/test_vector_quality.py tests/unit/test_semantic.py
git diff --check
git status --short
```

Ruff PASS. Không reset/checkout/stash/commit/push/merge. Git status cuối lưu cạnh báo cáo.

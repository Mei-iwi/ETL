# Kiểm thử và tái lập

Từ root repo. Các artifact corpus/vector/log chỉ ở `.test-tmp/semantic-quality` bị Git bỏ qua.
Không thay `.env`/`docker.env`, không tạo DB hoặc chạy semantic-schema/index --apply.
Baseline artifact/qrels/checkpoint đã có từ vòng trước được tái sử dụng; venv semantic cô lập giữ nguyên package.

## Offline ablation, validation lock, holdout

Đã chạy thành công, Python exit0:

```powershell
./.test-tmp/bge-semantic-venv/Scripts/python.exe scripts/optimize_semantic_quality.py prepare
./.test-tmp/bge-semantic-venv/Scripts/python.exe scripts/optimize_semantic_quality.py evaluate
```

Prepare dùng raw/title+content/full-context NPZ cũ nếu identity input+model+corpus khớp;
chỉ encode hai ablation mới và32 query mới. Tokenizer/model offline thực, không vector giả.
Evaluate chỉ dùng28 validation để chọn strategy/aggregation/rule; ghi selection-lock trước holdout20 câu.
Kết quả100 strategy/query rows + bảng calibration/aggregation trong private `results.json`.
Không đổi qrels hoặc tuning lại sau khi thấy holdout. Các lần tái lập giống hệt lock không phải holdout độc lập mới.

PowerShell5 có thể coi progress/warning stderr của thư viện là lỗi shell. Lần prepare thực đã dùng
Python subprocess để ghi stderr và trả đúng returncode; không thay warning thành lỗi model:

```powershell
./.venv/Scripts/python.exe -c "import subprocess; from pathlib import Path; p=Path('.test-tmp/semantic-quality/optimization'); p.mkdir(parents=True,exist_ok=True); log=(p/'prepare-stderr.log').open('w',encoding='utf-8'); r=subprocess.run(['.test-tmp/bge-semantic-venv/Scripts/python.exe','scripts/optimize_semantic_quality.py','prepare'],stderr=log); log.close(); raise SystemExit(r.returncode)"
```

## Cosine trước/sau — model vectors thật

```powershell
./.test-tmp/bge-semantic-venv/Scripts/python.exe scripts/benchmark_semantic_cosine.py
```

PASS trên30 query×363 vector thật, thứ tự Chunk giống hệt, delta score≤2,22e-16.
Đo riêng validation vector+similarity, không model inference/DB/HTTP. Lần đo bằng script versionable:
P95 trước235,95ms, sau82,51ms. Không dùng timing thăm dò chạy đồng thời workload model làm SLA.

## Model thật trong Docker

Image QA đã có từ trước; mount source/scripts/research hiện tại read-only, không tuyên bố image app cũ đã có patch mới.
Đã chạy đủ threads1/2/4 và batch1/2/4/8; kết quả trong RESULTS.profile:

```powershell
$repo=(Get-Location).Path
docker run --rm --workdir /app --network none --read-only `
  --tmpfs /tmp:rw,exec,uid=10001,gid=10001,size=536870912 --memory 4g --cpus 2 `
  --mount "type=bind,source=$repo/src,target=/app/src,readonly" `
  --mount "type=bind,source=$repo/scripts,target=/app/scripts,readonly" `
  --mount "type=bind,source=$repo/research,target=/app/research,readonly" `
  --mount "type=bind,source=$repo/Models/bge-m3,target=/opt/model,readonly" `
  --mount "type=bind,source=$repo/.test-tmp/semantic-quality,target=/evidence" `
  --entrypoint python etl-portable-backend:bge-chunk-qa-tests `
  -m scripts.profile_semantic_runtime --corpus /evidence/verified/corpus.json `
  --model-path /opt/model --output /evidence/optimization/docker-profile.json
```

Lần đầu thiếu `--workdir /app` nên không tìm được query spec; sửa working directory rồi PASS,
không cài thêm Git/tài liệu vào runtime hoặc thay app Compose để che lỗi.
Nhãn ordering được đính chính thành length ascending/descending theo đúng sample đã chạy, không sửa số đo.

Replay riêng passage sai ColBERT B2 với script versionable đã chạy PASS, exit0:

```powershell
./.test-tmp/bge-semantic-venv/Scripts/python.exe -m scripts.profile_semantic_runtime --colbert-only --output .test-tmp/semantic-quality/optimization/colbert-replay.json
```

6 passage/strategy comparisons, lấy đúng ID Chunk sai từ JSON baseline, không chọn theo tên hoặc hardcode penalty.
Kết quả riêng trong RESULTS.colbert_replay; study ban đầu/đối chiếu trong RESULTS.colbert_failure.
Replay này không phải holdout mới hoặc inference latency của API.

## Review nhãn private

```powershell
./.venv/Scripts/python.exe scripts/review_semantic_qrels.py export
./.venv/Scripts/python.exe scripts/review_semantic_qrels.py validate --reviews .test-tmp/semantic-quality/optimization/review.jsonl
```

Đã chạy:11.616 DRAFT,0 judged; không tự approve. Export lần nữa vào cùng file bị từ chối để giữ review của con người;
chọn output mới khi muốn chia theo query/reviewer. Evidence không vào Git.

## Backend/Docker hồi quy

Suite rộng **324 PASS,2 warnings**,144,31s,0 FAIL/0 SKIP. Mount scripts mới để tests review import được:

```powershell
$repo=(Get-Location).Path
docker run --rm --workdir /app --network none --read-only `
  --tmpfs /tmp:rw,exec,uid=10001,gid=10001,size=536870912 `
  --tmpfs /run/etl:rw,noexec,uid=10001,gid=10001,size=268435456 `
  --memory 3g --cpus 2 -e TMPDIR=/tmp `
  --mount "type=bind,source=$repo/src,target=/app/src,readonly" `
  --mount "type=bind,source=$repo/tests,target=/app/tests,readonly" `
  --mount "type=bind,source=$repo/scripts,target=/app/scripts,readonly" `
  --mount "type=bind,source=$repo/.test-tmp/semantic-quality/optimization,target=/evidence" `
  --entrypoint python etl-portable-backend:bge-chunk-qa-tests -m pytest `
  /app/tests/unit/test_semantic_selection.py /app/tests/unit/test_semantic_review.py `
  /app/tests/unit/test_vector_quality.py /app/tests/unit/test_semantic.py `
  /app/tests/unit/test_search.py /app/tests/unit/test_indexing.py `
  /app/tests/unit/test_search_access.py /app/tests/unit/test_owner_search_scope.py `
  /app/tests/unit/test_search_runtime_admission.py /app/tests/unit/test_http_search_query_cache.py `
  /app/tests/unit/test_evaluation.py /app/tests/unit/test_evaluation_metrics.py `
  /app/tests/unit/test_search_model_lifecycle.py /app/tests/unit/test_single_admin_auth.py `
  /app/tests/unit/test_master_auth.py /app/tests/unit/test_database_auth.py `
  -q --disable-warnings -p no:cacheprovider --junitxml=/evidence/docker-regression.xml
```

Guard không trộn version được thêm sau khi suite rộng đã thu thập tests; đã chạy lại riêng toàn bộ
`test_semantic_selection.py` trong cùng Docker QA: **15 PASS**,1 warning,1,47s; evidence `selection-final.xml`.
Không cộng324+15 thành339 tests độc lập vì có phần trùng nhau. Lần tái chạy suite rộng hiện tại có thêm test guard mới.
Các suite test DB dùng SQLite/memory fixture, không Dev/Production. Integration Mongo vật lý NOT_RUN:
chưa có DB test được chỉ định/cho phép; không tự cấp quyền hoặc tạo DB.

## Frontend và cuối cùng

```powershell
node --experimental-strip-types --test frontend/tests/searchApi.test.mjs frontend/tests/searchRuntime.test.mjs frontend/tests/searchModelStatus.test.mjs
npm.cmd --prefix frontend run build -- --outDir ../.test-tmp/semantic-quality/optimization/frontend-build
New-Item -ItemType Directory -Path .test-tmp/semantic-quality/optimization/ui -Force | Out-Null
Copy-Item -LiteralPath .test-tmp/semantic-quality/optimization/frontend-build -Destination .test-tmp/semantic-quality/optimization/ui/dist -Recurse
$env:ETL_T4_FRONTEND_ROOT=Join-Path (Get-Location) '.test-tmp/semantic-quality/optimization/ui'
node --test frontend/tests/searchUi.test.mjs
```

46 PASS; TypeScript/Vite build PASS; Edge fixture1 PASS7,58s. Dùng folder UI mới khi tái lập để không lồng `dist`.
Edge chạy ngoài sandbox do ràng buộc CDP đã biết; không dùng fixture để nghiệm thu Search live.

```powershell
./.venv/Scripts/python.exe -m ruff check --no-cache src/data_sync_etl/application/semantic.py src/data_sync_etl/application/semantic_selection.py scripts/optimize_semantic_quality.py scripts/profile_semantic_runtime.py scripts/review_semantic_qrels.py scripts/benchmark_semantic_cosine.py tests/unit/test_semantic.py tests/unit/test_semantic_selection.py tests/unit/test_semantic_review.py
git diff --check
git status --short --untracked-files=all
```

Live probe trực tiếp: GET /health200; GET /api/v1/search/model-status403 AUTH_PROXY_UNTRUSTED.
Không gửi query/session giả hoặc bypass. Thiếu session/proxy và index semantic hợp lệ:
authenticated Vector/Hybrid API, context, DB retrieval và HTTP latency **BLOCKED/NOT_RUN**.

Kế hoạch live: môi trường local/staging đã được cấp quyền, provision generation mới theo kế hoạch kiểm soát,
verify đủcoverage, login qua proxy thật; đo scopes admin/user/public/private, Vector/Hybrid, total/paging,
context/version, stale/revocation và latency. Rollback về flag semantic=false, giữ index/lịch sử;
không thay Auth hoặc chuyển production DB để lấy PASS.

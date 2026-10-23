# Kiểm chứng BGE-M3 — 23/10/2026

Branch `feat/search/bge-m3-contextual-retrieval`; HEAD `971496c`.
Source thay đổi được giữ trong working tree, không commit/push/merge.

## Kết quả

| Phép kiểm tra | Kết quả |
| --- | --- |
| Ruff trên toàn bộ Python thay đổi và tests mới | PASS |
| `git diff --check` | PASS |
| Hồi quy Windows cuối: HTTP query cache + semantic | 45 PASS |
| Runtime/launcher/native tests trên Windows | 42 PASS, 2 SKIP (native Linux) |
| Docker QA Python3.12 Linux, source cuối mount read-only | 238 PASS, 6 SKIP, 93.72s |
| Model thật, venv Windows cô lập | 1 PASS, 87.63s; Raw/Contextual/ColBERT/legacy cùng corpus |
| `pip check` trong venv semantic cô lập | PASS, No broken requirements found |
| Image runtime semantic, image QA | Build PASS; pip check trong image PASS |
| Docker BGE-M3/ColBERT, network none, model read-only | PASS ở4GB và image cuối3GB/2CPU:1024D, norm1, ColBERT finite |
| Compose auth + SOURCE Neon + workers, config --quiet | PASS; không kết nối DB |
| Frontend TypeScript/Vite production build | PASS, 1995 modules |
| Mongo vật lý/lease/strict validator/end-to-end model→Mongo | SKIP: chưa có DB test local được chỉ định |
| Kho thật + ground truth đã xác nhận | SKIP: chưa có corpus/qrels được duyệt |
| Restart Docker với Mongo thật, GPU, load test corpus lớn | SKIP: chưa có fixture/thiết bị tương ứng |

Không tính PASS cho các phép thử SKIP. Tests dùng SQLite/memory store chỉ chứng minh orchestration;
không thay thế integration Mongo vật lý. Không tạo database, chạy migration hoặc backfill kho đang hoạt động.
6 SKIP trong Docker:2 integration Mongo chưa có DB test,4 launcher PowerShell vì Linux không có powershell.exe.
Các launcher Windows tương ứng đã chạy PASS ở nhóm kiểm thử host.
JUnit lưu kèm tại [docker-qa.xml](docker-qa.xml).

Lỗi đã phát hiện và xử lý:

- FlagEmbedding1.3.5 + Transformers5 lỗi import `is_torch_fx_available`: dùng extra/constraint semantic riêng.
- Response legacy có thêm `vector_index:null`: serializer chỉ phát trường này khi nhánh semantic dùng thật;
  kiểm thử parity HTTP/direct và pagination đã PASS lại.
- QA launcher stub nằm trên tmpfs noexec: chạy fixture trên `/tmp` exec của **container QA cô lập**;
  không sửa mount/security của Compose production.
- Venv thử ban đầu kế thừa package hệ thống gây xung đột botocore: tắt kế thừa, cài dependencies trong
  venv riêng rồi kiểm tra lại PASS. Không sửa package Python hệ thống.

## Model thật: kết quả smoke

Evidence: [smoke-20261023.json](smoke-20261023.json), được sinh bởi
`tests/integration/test_semantic_model.py`; không có vector giả trên đường này.

4 văn bản tự tạo: phân số, quang hợp, bảo vệ tài khoản và tách muối. 6 truy vấn Việt, Anh và trộn ngôn ngữ.
Đây **không phải** corpus học liệu thật hoặc kết quả nghiệm thu nghiên cứu tổng quát.
Tất cả phương pháp dùng cùng corpus, query, nhãn, model revision; legacy là adapter thực hiện tại
với BGE-M3 và chuỗi keywords của mỗi tài liệu một leaf/một Bag. Không đổi sang MiniLM cho baseline.

| Chỉ số | Legacy keywords | BGE Raw | BGE Contextual | Contextual + ColBERT |
| --- | ---: | ---: | ---: | ---: |
| Top1 đúng | 6/6 | 6/6 | 6/6 | 6/6 |
| Precision@5 | 0.20 | 0.20 | 0.20 | 0.20 |
| Recall@5 | 1.00 | 1.00 | 1.00 | 1.00 |
| MRR@10 | 1.00 | 1.00 | 1.00 | 1.00 |
| nDCG@10 | 1.00 | 1.00 | 1.00 | 1.00 |
| Retrieval P50 ms | 2.42 | 3.12 | 2.46 | 3761.24 |
| Retrieval P95 ms | 15.72 | 5.35 | 3.17 | 4978.38 |
| Tạo vector4 tài liệu, giây | 1.35 | 2.28 | 2.99 | 2.99, dùng lại Dense |
| CPU encode tài liệu, giây | 2.63 | 4.34 | 5.83 | 5.83, chưa tính rerank |
| RSS quan sát, GiB | 1.71 | 1.17 | 1.18 | 1.18 |

P50/P95 này chỉ gồm cosine/ranking và rerank nếu bật, **không phải latency API**.
Query embedding đo riêng: BGE-M3 chung6 query=7.732s; legacy=1.913s. Lần gọi đầu/caching và chạy
đồng thời với Docker build ảnh hưởng timing; đây là một smoke run, chưa phải benchmark được kiểm soát.
RSS là snapshot process ở từng bước, không phải peak hoặc chênh lệch RAM độc lập giữa các mô hình.

16KiB/vector set4 tài liệu trong JSON là payload float32 lý thuyết. Mongo thực lưu BSON double có
metadata/array/index overhead; kích thước index Mongo vật lý **chưa đo**.
P@5 có mẫu số5 dù corpus chỉ4 tài liệu; mỗi query chỉ có một positive.
Không có bằng chứng Contextual cải thiện chất lượng trên bộ này. ColBERT chậm hơn và vẫn tắt mặc định.

## Lệnh kiểm tra

Từ root repo. Không thay các URL database đang chạy để chạy tests.

```bash
python -m pytest tests/unit/test_semantic.py tests/unit/test_indexing.py tests/unit/test_search.py tests/unit/test_owner_search_scope.py tests/unit/test_search_access.py tests/unit/test_search_runtime_admission.py tests/unit/test_http_search_query_cache.py tests/unit/test_evaluation.py tests/unit/test_evaluation_metrics.py tests/unit/test_evaluation_history.py tests/integration/test_semantic_mongo.py -q --disable-warnings -p no:cacheprovider
python -m pytest tests/unit/test_http_search_query_cache.py tests/unit/test_semantic.py -q --disable-warnings -p no:cacheprovider
python -m pytest deploy/tests/test_runtime.py deploy/tests/test_launcher_env.py deploy/tests/test_native_runtime.py -q --disable-warnings -p no:cacheprovider
python -m ruff check --no-cache src/data_sync_etl/adapters/bge_m3.py src/data_sync_etl/adapters/mongo/semantic.py src/data_sync_etl/adapters/persistence/search_reader.py src/data_sync_etl/application/semantic.py src/data_sync_etl/application/search.py src/data_sync_etl/application/evaluation.py src/data_sync_etl/domain/semantic.py src/data_sync_etl/ports/semantic.py src/data_sync_etl/ports/search.py src/data_sync_etl/api/search_schemas.py src/data_sync_etl/api/routes.py src/data_sync_etl/config.py src/data_sync_etl/composition.py src/data_sync_etl/cli/main.py src/data_sync_etl/main.py tests/semantic_support.py tests/unit/test_semantic.py tests/integration/test_semantic_model.py tests/integration/test_semantic_mongo.py
npm.cmd --prefix frontend run build -- --outDir ../.test-tmp/bge-semantic-frontend-final
git diff --check
```

Hồi quy Windows rộng trước serializer fix:199 PASS,1 FAIL,2 SKIP; sau fix chạy lại đúng nhóm lỗi và
semantic=45 PASS. Bộ Docker cuối chạy lại toàn bộ selection tương ứng trên Python3.12 và dependency semantic.

Model thật trong venv riêng đã provision dependencies; PowerShell:

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
$env:OMP_NUM_THREADS='2'
$env:BGE_M3_TEST_PATH=(Join-Path (Get-Location) 'Models/bge-m3')
$env:BGE_M3_TEST_REVISION='5617a9f61b028005a4858fdac845db406aefb181'
$env:BGE_M3_TEST_REPORT=(Join-Path (Get-Location) '.test-tmp/bge-m3-contextual/smoke-isolated.json')
./.test-tmp/bge-semantic-venv/Scripts/python.exe -m pytest tests/integration/test_semantic_model.py -q --disable-warnings -p no:cacheprovider
./.test-tmp/bge-semantic-venv/Scripts/python.exe -m pip check
```

Docker build/check đã chạy, không Start/Check launcher vì các bước đó có thể kết nối SOURCE Neon:

```bash
docker build --build-arg INSTALL_SEMANTIC=true --target runtime -f deploy/docker/backend.Dockerfile -t etl-portable-backend:bge-chunk-final .
docker build --build-arg INSTALL_SEMANTIC=true --target qa -f deploy/docker/backend.Dockerfile -t etl-portable-backend:bge-chunk-qa-tests .
docker compose --env-file docker.env -f deploy/compose.app.yml -f deploy/compose.auth.yml -f deploy/compose.source-neon.yml --profile workers config --quiet
```

Docker QA: image chứa dependencies/tests, source cuối mount read-only để gồm serializer fix.
`TMPDIR=/tmp` và tmpfs exec chỉ phục vụ shell stub trong launcher test. Không mount secrets/DB hoặc publish port.
JUnit được lưu tại `.test-tmp/bge-m3-contextual/docker-qa.xml`.

```bash
docker run --rm --network none --read-only --tmpfs /tmp:rw,exec,uid=10001,gid=10001,size=536870912 --tmpfs /run/etl:rw,noexec,uid=10001,gid=10001,size=268435456 --memory 3g --cpus 2 -e TMPDIR=/tmp --mount "type=bind,source=D:/StudyMaterials/HK7/Final_Project/ETL/src,target=/app/src,readonly" --mount "type=bind,source=D:/StudyMaterials/HK7/Final_Project/ETL/.test-tmp/bge-m3-contextual,target=/evidence" --entrypoint python etl-portable-backend:bge-chunk-qa-tests -m pytest /app/tests/unit/test_semantic.py /app/tests/unit/test_indexing.py /app/tests/unit/test_search.py /app/tests/unit/test_owner_search_scope.py /app/tests/unit/test_search_access.py /app/tests/unit/test_search_runtime_admission.py /app/tests/unit/test_http_search_query_cache.py /app/tests/unit/test_evaluation.py /app/tests/unit/test_evaluation_metrics.py /app/tests/unit/test_evaluation_history.py /app/tests/integration/test_semantic_mongo.py /app/deploy/tests/test_runtime.py /app/deploy/tests/test_launcher_env.py -q --disable-warnings -p no:cacheprovider --junitxml=/evidence/docker-qa.xml
```

Đường dẫn host trong lệnh evidence này là máy đã kiểm thử; máy khác thay bằng root repo tương ứng.
Smoke image cuối đã chạy với `--network none --read-only --memory 3g --cpus 2`, mount model read-only,
không mount source/secret/database; kết quả JSON:

```json
{"status":"PASS","dimension":1024,"norm":1.0,"colbert_finite":true,"offline":true,"memory_limit":"3g"}
```

BuildKit có cảnh báo heuristic `AUTH_MODE` chứa từ AUTH; giá trị là mode công khai, không phải credential.
Không sửa hoặc nới Auth để loại cảnh báo.

## Git và giới hạn nghiệm thu

Danh sách thay đổi và hàm chính: [README BGE-M3](README.md#b-changes-made).
Git status cuối: [git-status.txt](git-status.txt).
Working tree ban đầu sạch. HEAD/branch không thay đổi, không có staging/commit/push/merge trong task.
`.env`, `docker.env`, credentials, SOURCE/Master/Mongo schema cũ và dữ liệu model/managed files được giữ nguyên.
Integration Mongo, corpus thật, quyền thu hồi trên deployment thật và restart bền vững cần nghiệm thu riêng
trước khi bật feature trên môi trường sử dụng. Trạng thái tổng thể **PARTIAL**.

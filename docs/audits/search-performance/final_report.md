# Báo cáo cuối — Search / Evaluation performance

17/10/2026. **Đã triển khai và kiểm thử. Người dùng đã yêu cầu tách commit và “commit push lên luôn” sau khi được báo blocker governance; tiếp tục xuất bản theo lệnh mới này. Governance vẫn BLOCKED, không được đánh dấu PASS.**

## Kết quả đo

Đã bỏ prepare trùng trong Search Vector/Hybrid với adapter local thật và dùng một query vector cho Vector/Hybrid trong Evaluation. Model vốn đã được giữ trong bộ nhớ; không tuyên bố sửa lỗi tải weights mỗi request.

So sánh luân phiên trên **cùng fixture, cùng weights/revision/CPU**, 3 batch × 18 câu × 3 method: **36 → 18 encode, 72 → 36 prepare/readiness**. Batch cuối có tổng encode **389.45 → 200.05 ms**. Giữ nguyên 111 marker checks, 595 snapshot checks, 100 eligibility reads, 2 full corpus checks và 54 Search/candidate calls.

Median tổng Evaluation **3414.42 → 3413.72 ms**, gần như không đổi; p95 **3458.03 → 3420.44 ms**, chỉ 3 mẫu/bên. **Chưa chứng minh giảm latency tổng ổn định.** Giữ thay đổi vì loại bỏ công việc mã hóa lặp có bằng chứng; không quảng bá % tăng tốc end-to-end. Snapshot/guard vẫn chiếm phần lớn thời gian.

### Search độc lập, warm (54 mẫu/method/bên, ms)

| Method | Median trước | Median sau | p95 trước | p95 sau |
|---|---:|---:|---:|---:|
| BoW | 29.95 | 32.61 | 52.65 | 48.27 |
| Vector | 60.80 | 60.52 | 68.33 | 67.59 |
| Hybrid | 60.45 | 61.18 | 77.80 | 68.44 |

Search độc lập vẫn encode mỗi request. BoW/Hybrid median dao động tăng; không kết luận mọi method nhanh hơn. Min/max và các stage inclusive nằm trong JSON; không cộng stage lồng nhau.

### Per-method trong Evaluation (54 mẫu/method/bên, ms)

| Method | Median trước | Median sau | p95 trước | p95 sau |
|---|---:|---:|---:|---:|
| BoW | 27.96 | 30.45 | 47.11 | 50.76 |
| Vector | 60.36 | 59.60 | 70.43 | 71.72 |
| Hybrid | 60.87 | 54.26 | 65.33 | 69.44 |

Thứ tự method xoay theo query như cũ. Method cần vector chạy trước chịu chi phí encode; method thứ hai dùng lại vector. Không diễn giải latency này như benchmark model độc lập.

## G0 — Baseline và cách so sánh

- Base (HEAD lúc đo nền) `9437969e3c9ea48615d1f1d33ecbecabfb595951`, chứa PR #64/B04/B09 `9695cbf` (ancestor exit 0). Nhánh riêng `perf/etl/search-evaluation-model-runtime`, worktree `.worktrees/search-performance`.
- `baseline.md`, `baseline-double.json`, `baseline-real.json` được đo trước production changes. `after-double.json` là orchestration double; `after-real.json` là model local thật.
- Model CPU/384 chiều, revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, OMP/MKL=2, 1 process; Python 3.13.0, torch 2.14.1, sentence-transformers 5.7.0.
- Fixture: 6 phiên bản/13 Bag, **SQLite tạm + typed in-memory Mongo double**, 18 truy vấn tổng hợp, không dùng query/corpus Dev. Stage `pg.*`/`mongo.*` là vị trí logic, không phải latency DB thật. Warm-up riêng; prefix k=1/3/5 kiểm tra ngoài mẫu Search warm. Thêm 24 chuỗi Việt/Unicode bằng model thật, finite/384.
- Paired comparison đọc mã commit cố định qua `git show` vào thư mục test tạm, dùng cùng fixture và weights đã nạp, luân phiên trước/sau. Hash baseline raw checkout CRLF khác hash Git object LF; chuyển LF→CRLF khớp chính xác cả 3 file. Hash final code khớp `after-real.json`.
- **Ranked IDs, thứ tự, scores và metrics tại k=1/3/5 giống chính xác, tolerance 0.** Snapshot giống nhau trừ `search_version` đổi có chủ đích theo code hash. Hash corpus của hai fixture tạo độc lập không dùng làm bằng chứng bằng nhau.
- Cold prepare ban đầu 10.97 s, lần đo cuối 8.02 s; OS file cache/import state không kiểm soát, **không quy chênh lệch này cho tối ưu**. Không preload startup/thread nền/model download.

## G1 — Lifecycle và readiness

`LocalSentenceEmbedding.prepare_query()` chuẩn bị một lần rồi trả encoder chỉ dùng ngay trong request. `embed()` trực tiếp vẫn tự prepare; Search giữ timeout warmup/query hiện có. Lock tải/probe vẫn bảo đảm một instance mỗi process, lỗi load có thể thử lại. Đổi path/model ID/revision/dimension sau khi nạp bị từ chối; ready không báo nhầm.

Không cache readiness xuyên request: kiểm tra manifest/modules/weights hiện có vẫn chạy, không hash lại toàn bộ weights mỗi request. Không có cache query LRU/TTL toàn cục; eviction/expiry NOT_APPLICABLE. Không thêm cấu hình/chỉnh `.env`/model files. Nhiều worker vẫn có model riêng; RAM nhiều worker NOT_MEASURED.

## G2 — Query context và consistency

`QueryEmbeddingContext` thuộc đúng một query trong vòng Evaluation tuần tự. Key là digest của query đã NFC+trim, model ID/revision/dimension; kiểm tra thêm chính instance adapter. Encode CPU/default normalization giữ nguyên, không có encode setting mới. Tối đa một tuple vector, trả copy; clear khi lỗi method hoặc thoát context, kể cả generator close. Không giữ raw query trong cache/log hoặc reuse giữa query/run/user độc lập.

Batch đo có 18 miss + 18 hit tại context Vector/Hybrid; request độc lập không có cache hit. Xác minh model vẫn chạy trước hit; permission chạy trước model/context. Metadata, stale Bag, allowed units/ancestor và kiểm tra sau ranking giữ nguyên. Corpus mutation vẫn NOT_COMPARABLE và xóa metrics theo hợp đồng cũ.

Evaluation đã tìm một lần ở max_results rồi cắt k từ baseline; không tuyên bố giảm giả từ 162 xuống 54 Search. API hiện bắt buộc đủ 3 method, BoW-only Evaluation không thuộc contract hiện tại (giữ 422). Search BoW độc lập không prepare/load model, kể cả runtime unavailable.

Không gộp guards, bỏ kiểm tra Bag, tạo SQL/Mongo index/projection/schema hay tăng concurrency. Profile benchmark dùng SQLite/double chưa đủ chứng minh tối ưu retrieval trên PG/Mongo thật. Dataset code cũ có thể stale vì fingerprint đổi: cần kiểm tra/tạo lại dataset, không bỏ validation.

## G3 — Giao diện và browser fixture

- Search khóa nút trong lúc chờ; ref chặn submit/retry liên tiếp. Giữ microtask StrictMode, abort và chống phản hồi cũ khi chuyển trang. Loading chung/aria-busy, không tự gán pha khởi tạo model.
- Evaluation indeterminate trước progress backend; phần trăm sau đó lấy NDJSON thật. Giữ ref/fieldset chống double-submit. Chú thích rời trang chỉ ngừng chờ, server có thể còn xử lý; không báo CANCELLED server từ thao tác client.
- Layout/favicon/API/SearchAccess/errors giữ nguyên.
- Fixture chia NDJSON theo sự kiện để không phụ thuộc TCP coalescing; kiểm tra 18×3 progress. Selector Indexing cập nhật theo UI baseline hiện tại. Server fixture xác minh loopback và khai báo no-proxy-headers; không sửa policy production.

## Kiểm thử thực chạy

| Check | Kết quả |
|---|---|
| Full unit | 1048 PASS, 1 FAIL governance thiếu ảnh, 1 SKIP symlink Windows |
| Search/Evaluation/Indexing focused | 101 PASS |
| Runtime/context mới | 14 PASS |
| Sau readiness change cuối: runtime/SearchAccess/Indexing | 91 PASS |
| Integration isolated ban đầu, real model/OCR/browser | 235 PASS, 4 FAIL browser fixture, 1 XFAIL lịch sử |
| Sau sửa fixture | Evaluation UI 18 câu: 1 PASS; Indexing UI structured/fallback/mixed: 3 PASS |
| Integration distinct sau xử lý lỗi | 239 bài đã PASS, 1 XFAIL; không tuyên bố một lần chạy toàn bộ cuối cùng đều xanh |
| Benchmark double + real opt-in trước/sau | PASS; paired order/score/metrics tolerance 0 |
| Frontend Node tests gồm Edge fixture | 70 PASS, 0 SKIP |
| Frontend build/lint | PASS, 5 warning React có sẵn, không thêm warning |
| Ruff `src tests scripts`, git diff --check | PASS |
| Governance checker | BLOCKED/exit 1: `Required reference missing: ERD_SOURCE_GOC_15_BANG.jpg` |

XFAIL migration 0005 là KNOWN_DEFECT cũ, không sửa/xfail thêm. FutureWarning tên method sentence-transformers có sẵn. Bốn lỗi browser ban đầu được giữ trong lịch sử kiểm thử và chạy lại đúng bốn bài sau sửa, không che bằng skip.

Integration dùng PostgreSQL 16/MongoDB 8.0 trong container mới có nhãn `etl.search-performance.test=true`, random loopback ports, tmpfs, schema/DB UUID. Đã xác minh ownership và dọn đúng hai container của phiên này. Không dùng prune/down -v hoặc volume Dev.

## Schema, quyền và NOT_RUN

`schema-preservation.json`: 24 hash schema/migration khớp frozen baseline; SOURCE 15/Master 18/Mongo 5, heads s0003/0015. Đây là kiểm tra implementation riêng, **không thay thế governance**.

Thiếu cả 8 tài liệu gốc trong `TAI_LIEU_DOI_CHIEU`, liệt kê trong JSON. Cần đưa đúng bộ gốc về vị trí checker yêu cầu rồi chạy checker/hash lại để hoàn tất governance. Người dùng đã cho phép commit/push trước khi blocker này được giải quyết. Không tạo bản giả hoặc sửa frozen baseline.

Pytest dùng runner chặn dotenv và model config truyền riêng. Không đọc `.env` thật/ghi model, SOURCE files hoặc DB Dev. Legacy VECTOR_DIMENSION 256 và index 384 giữ nguyên. Các model/vector double chỉ nằm trong tests.

Production auth/tenant/OAuth, Drive live, human Ground Truth/quality: **NOT_RUN**. Metrics synthetic chỉ kiểm tra không đổi công thức, không phải bằng chứng chất lượng. GPU, RAM peak, nhiều worker, latency PG/Mongo thật, API cold end-to-end, tổng ranking tách riêng, tokenize riêng: NOT_MEASURED.

## Lệnh và Git bàn giao

Từ root worktree: Python `../../.venv/Scripts/python.exe`, `PYTHONPATH=src`. Runner `scripts/run_b04_b09_tests.py -p no:cacheprovider -q --tb=short` với các mục tiêu `tests/unit`, `tests/integration --ignore=tests/integration/test_google_drive_readonly.py`, `tests/performance/test_search_latency.py`. Cách opt-in benchmark/model offline: xem baseline.md.

Integration truyền riêng TEST_DATABASE_URL/TEST_SOURCE_DATABASE_URL trỏ DB test đã xác minh và TEST_MONGO_URI; ETL_REQUIRE_POSTGRES=1. Bật ETL_TEST_REAL_EMBEDDING, ETL_TEST_REAL_UI, ETL_SEARCH_REAL_MODEL, ETL_EVALUATION_UI, ETL_EVALUATION_REAL_MODEL. Không lấy URL/credential từ `.env`.

Các check khác: `python -m ruff check src tests scripts`; `python scripts/check_migration_governance.py`; `node --experimental-strip-types --test frontend/tests/*.test.mjs`; `npm --prefix frontend run build`; `npm --prefix frontend run lint`; `git diff --check`.

**Kiểm thử chức năng đạt; governance vẫn BLOCKED/PARTIAL.** Ngoại lệ commit/push đến từ lệnh trực tiếp tiếp theo của người dùng, không phải checker PASS. Tách thành 4 commit:

- `54179b2 test(perf): establish search and evaluation latency baselines`
- `08a1967 perf(embedding): reduce duplicate model preparation in search`
- `371d0b7 perf(evaluation): reuse query embeddings across vector methods`
- G3, commit chứa báo cáo này: `perf(frontend): clarify loading and prevent duplicate submissions`.

Push nhánh `perf/etl/search-evaluation-model-runtime` sau commit G3, không merge vào main; SHA cuối và remote được xác minh khi bàn giao. Không force push/rebase/reset/restore/stash/cherry-pick. Stash rỗng trước/sau; worktree chính giữ thay đổi UI người dùng, B04/B09 giữ commit 9695cbf. Báo cáo nằm trong G3, không tạo commit thứ năm.

Kiểm tra trước xuất bản: 76 PASS cho runtime/Search/Evaluation/benchmark; snapshot stage G1 riêng có 75 PASS Search/Indexing. Dữ liệu benchmark và cảnh báo latency không đổi, không coi giảm encode là bằng chứng tăng tốc tổng ổn định.

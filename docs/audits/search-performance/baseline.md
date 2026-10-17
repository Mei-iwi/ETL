# G0 — Baseline Search / Evaluation

Ngày đo: 17/10/2026. Base `9437969e3c9ea48615d1f1d33ecbecabfb595951` (PR #64), đã chứa B04/B09 `9695cbf` (kiểm tra ancestor thành công).
Nhánh riêng: `perf/etl/search-evaluation-model-runtime`; worktree `search-performance`.
Không lấy thay đổi chưa commit của worktree chính.

## Call graph và lifecycle

- Startup tạo một Container / LocalSentenceEmbedding mỗi process; không tải model.
- Search: normalize NFC + trim → SearchAccess → schema readiness → prepare → embed (prepare lần nữa) → eligibility → candidates → snapshot/allowed units/snippets → kiểm tra lại từng Bag → cosine/BoW/RRF → group/tie-break → kiểm tra lại quyền/snapshot → phân trang.
- Evaluation: khóa run → full corpus + marker → 18 query, xoay thứ tự 3 method → marker trước/sau từng Search → một Search tại max_results/method → metrics bằng prefix k=1/3/5 → full corpus + marker cuối.
- Không có result cache. Trọng số được giữ trong `_model`; lock ngăn nạp đồng thời. Không nạp lại weights mỗi truy vấn.
- Frontend Search dùng microtask bỏ probe POST của StrictMode, có abort/response guard. Evaluation có ref chống double-submit và NDJSON progress thật.

## Phạm vi đo

`baseline-double.json`: orchestration double 3 chiều, không phải benchmark embedding.
`baseline-real.json`: model local thật CPU, 384 chiều, revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, OMP/MKL=2, một process; Python/package versions nằm trong JSON.
Cả hai dùng **SQLite tạm + typed in-memory Bag store**, 6 phiên bản / 13 Bag / 18 truy vấn tổng hợp. Tên stage `pg.*`/`mongo.*` chỉ vị trí adapter logic; không phải latency PostgreSQL/MongoDB thật hoặc corpus Dev. Query hashes thay raw query trong kết quả.

Warm-up 3 method tách riêng; mỗi method 36 mẫu warm (18 × 2). Kiểm tra prefix k=1/3/5 nằm ngoài mẫu latency. Stage timings inclusive, không cộng các stage lồng nhau. JSON lưu min/median/p95/max và code/dataset/corpus hashes.

| Baseline real | Median (ms) | p95 (ms) |
|---|---:|---:|
| BoW warm | 41.76 | 83.05 |
| Vector warm | 69.65 | 116.00 |
| Hybrid warm | 71.53 | 111.52 |

Cold prepare: 10.97 giây, gồm import + weights + probe trong process mới; OS file cache không được kiểm soát. Evaluation 18 × 3: 36 encode, 72 prepare/readiness, 111 corpus marker, 54 candidates calls. Snapshot/guard chiếm phần lớn chi phí; encode giữa Vector/Hybrid là công việc lặp có thể bỏ. Evaluation đã lấy max_results một lần, không chạy lại theo từng k.

Không đặt ngưỡng performance cứng trên máy dùng chung. GPU, RAM peak, nhiều worker, HTTP cold và tách tokenize khỏi encode: NOT_MEASURED. Production auth/OAuth và human Ground Truth: NOT_RUN.

## Chạy lại không đọc .env

Từ root worktree, dùng Python venv và `PYTHONPATH=src`; runner chặn đọc dotenv thật:

```powershell
$env:PYTHONPATH=(Resolve-Path src).Path
$env:ETL_PERF_REPEATS='2'
$env:ETL_PERF_OUTPUT='docs/audits/search-performance/reproduced-double.json'
../../.venv/Scripts/python.exe scripts/run_b04_b09_tests.py -p no:cacheprovider -q tests/performance/test_search_latency.py
```

Opt-in real: thêm `ETL_PERF_REAL_MODEL=1`, model path/id/revision bằng cấu hình riêng không lấy từ `.env`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`. Không tải model từ Internet.
`ETL_PERF_COMPARE_BASE=1` đọc mã commit cố định qua `git show` vào thư mục test tạm để so sánh luân phiên trên cùng fixture/weights. Không đổi trạng thái Git. CI mặc định dùng double.

## Gate G0

Benchmark PASS; governance BLOCKED: `Required reference missing: ERD_SOURCE_GOC_15_BANG.jpg` (8 tài liệu gốc vắng mặt). Không tạo tài liệu giả hoặc chỉnh frozen baseline. G0 ban đầu chưa commit do blocker này; lệnh tiếp theo của người dùng đã yêu cầu commit/push với ngoại lệ này được ghi rõ, không coi governance là PASS. Kiểm tra hash implementation riêng không thay thế governance.

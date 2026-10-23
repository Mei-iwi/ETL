# Thay đổi trong vòng tối ưu

Giữ nguyên nhánh `feat/search/bge-m3-contextual-retrieval`, HEAD `971496c`. Giữ toàn bộ dirty work trước đó.

| File | Nội dung |
| --- | --- |
| `src/data_sync_etl/application/semantic.py` | Giữ vector đã chuẩn hóa sau validation bằng bản sao trong memory; chuẩn hóa query một lần mỗi tập phiên bản; dot product trên hai vector đã kiểm chứng. Không đổi score contract, schema, generation hoặc vector lưu trữ. |
| `src/data_sync_etl/application/semantic_selection.py` | Rule offline absolute/margin/selective, calibration validation-only, ràng buộc recall/false-abstention; bounded aggregation và score buckets riêng. Không nối runtime. |
| `scripts/optimize_semantic_quality.py` | Tái sử dụng artifact đúng identity; hai ablation mới; khóa lựa chọn validation trước holdout; báo resource/chunk/snippet tách riêng. |
| `scripts/profile_semantic_runtime.py` | Profile model thật trong process/container riêng: cold/warm encode, threads, batch/ordering, sampled peak RSS, Unicode/overflow, ColBERT token interactions; không DB/Auth bypass. |
| `scripts/benchmark_semantic_cosine.py` | Tái lập before/after trên frozen vectors thật, kiểm tra score/rank parity và P50/P95, không truy cập DB. |
| `scripts/review_semantic_qrels.py` | Export full evidence private/blinded; validate human status, evidence identity và conflicts; không tự approve. |
| `tests/unit/test_semantic.py` | Kiểm tra reuse normalization không sửa dữ liệu hoặc nới validation. Hai correctness regression trước đó được giữ. |
| `tests/unit/test_semantic_selection.py` | No-answer/partial, risk constraints, leakage prevention, stable bounded aggregation, locator và unjudged. |
| `tests/unit/test_semantic_review.py` | Không overwrite human review, không export private body vào repo, conflicts/tamper/approval validation. |
| `research/semantic-quality/optimization/` | Bộ query/qrels version mới, freeze, kết quả, profile, lệnh và báo cáo A–L. |
| `README.md` | Liên kết tới vòng tối ưu/no-answer mới. |

Không sửa BoW/extractor/TF-IDF/RRF, Auth, frontend response contract, SOURCE/MASTER, OCR hoặc source hash/storage key.
Không bật semantic/rerank/threshold, cài schema, chạy live reindex, commit/push/merge/deploy.
Candidate/abstention thử nghiệm không đạt gate tổng quát nên chỉ giữ trong evaluator.

Rollback code chỉ cần bỏ riêng phần reuse-normalization trong `SemanticService`; vector/index identity không đổi,
không có migration hay dữ liệu cần phục hồi. Không reset/restore working tree để rollback các thay đổi khác.

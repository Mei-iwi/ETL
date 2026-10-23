# Phạm vi thay đổi của nhiệm vụ đánh giá chất lượng

Nhánh và HEAD giữ nguyên. Workspace đã có thay đổi từ nhiệm vụ Chunk Semantic và sửa shutdown trước đó;
không quy toàn bộ Git diff hiện tại cho nhiệm vụ này.

| File | Thay đổi lần này |
| --- | --- |
| `src/data_sync_etl/application/semantic.py` | Bỏ qua phiên bản không có nội dung được phép trước kiểm tra manifest; đa dạng phiên bản trước ngân sách ColBERT |
| `src/data_sync_etl/domain/semantic.py` | Helper chọn ứng viên ổn định theo phiên bản, dùng chung service/evaluator |
| `src/data_sync_etl/application/vector_quality.py` | Metric resource/chunk, no-answer, qrels evidence, calibration validation-only, grouping và score distribution |
| `scripts/export_semantic_quality.py` | Export snapshot MASTER/Mongo loopback READ ONLY, bounded, output private |
| `scripts/evaluate_semantic_quality.py` | Runner BGE-M3 offline, checksum/model contract, ablations, TopK/ColBERT và timings |
| `tests/unit/test_semantic.py` | Hai test tái hiện lỗi rồi kiểm tra bản sửa |
| `tests/unit/test_vector_quality.py` | Mười test metric, nhãn, threshold, dedup và invalid score |
| `frontend/tests/searchUi.test.mjs` | Cập nhật selector nút quay lại theo SVG và text hiện tại |
| `README.md` | Thêm liên kết đến báo cáo chất lượng |
| `research/semantic-quality/` | Query specification, qrels ID, kết quả không chứa corpus/vector, audit, validation và Git status |

Giữ nguyên thay đổi trước đó ở embedding runtime/lifecycle, semantic adapters/ports, CLI/composition/config,
Search/API contracts, Docker opt-in, frontend types và tài liệu `research/bge-m3/`.
Xem danh sách cụ thể toàn workspace trong [git-status.txt](git-status.txt).

Không sửa `.env`, `docker.env`, secret, dữ liệu/schema, BoW/extractor/TF-IDF/RRF, SOURCE key/hash hoặc file nguồn.
Không install schema/reindex trên Mongo live, không thay threshold hoặc bật semantic/rerank mặc định.
Corpus, NPZ và raw experiment logs được giữ trong `.test-tmp/semantic-quality/` bị Git bỏ qua.
